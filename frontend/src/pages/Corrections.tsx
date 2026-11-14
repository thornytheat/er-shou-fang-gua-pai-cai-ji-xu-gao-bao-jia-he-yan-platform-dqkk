import { useEffect, useState, useSyncExternalStore } from 'react';
import { correctionsApi, type CorrectionItemInput } from '../api/corrections';
import { useToast } from '../hooks/useToast';
import { authStore } from '../stores/authStore';
import { correctionsStore } from '../stores/correctionsStore';
import { can } from '../constants/roles';
import { CORRECTION_STATUS, CORRECTION_STATUS_LABELS, CORRECTION_STATUS_ORDER } from '../constants/enums';
import { dateText, yuan } from '../utils/format';
import { errorMessage } from '../utils/errorMessage';
import EmptyState from '../components/common/EmptyState';
import ErrorState from '../components/common/ErrorState';
import LedgerTable from '../components/common/LedgerTable';
import MetricStrip from '../components/common/MetricStrip';
import StampTag from '../components/common/StampTag';
import type { CorrectionBatchRow } from '../types/models';

const S = CORRECTION_STATUS;

/** 幂等键：一次表单会话一个，双击 / 重试 / 网络重发都命中同一批次。 */
function newRequestKey(): string {
  const rand = Math.random().toString(36).slice(2, 10);
  return `web-${Date.now()}-${rand}`;
}

interface DraftRow {
  listing_id: string;
  target_price: string;
}

export default function Corrections() {
  const auth = useSyncExternalStore(authStore.subscribe, authStore.getState);
  const role = auth.user?.role;
  const canSubmit = can(role, 'correction:submit');
  const canApprove = can(role, 'correction:approve');
  const toast = useToast();

  const status = useSyncExternalStore(correctionsStore.subscribe, correctionsStore.getStatus);
  const items = useSyncExternalStore(correctionsStore.subscribe, correctionsStore.getItems);
  const counts = useSyncExternalStore(correctionsStore.subscribe, correctionsStore.getCounts);
  const current = useSyncExternalStore(correctionsStore.subscribe, correctionsStore.getCurrent);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [note, setNote] = useState('');
  const [rows, setRows] = useState<DraftRow[]>([{ listing_id: '', target_price: '' }]);
  const [requestKey, setRequestKey] = useState(newRequestKey);

  async function load() {
    setLoading(true);
    try {
      await correctionsStore.load();
      setError(null);
    } catch (err) {
      setError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, [status]);

  async function open(batchId: number) {
    try {
      await correctionsStore.open(batchId);
    } catch (err) {
      toast.error(errorMessage((err as { payload?: unknown }).payload, '批次详情读取失败'));
    }
  }

  async function act(action: 'approve' | 'publish' | 'retry' | 'rebase' | 'withdraw', batch: CorrectionBatchRow) {
    setBusy(action);
    try {
      const updated = await correctionsApi[action](batch.id);
      correctionsStore.applyBatch(updated);
      await correctionsStore.load();
      const label = updated.status_label;
      if (updated.status === S.CONFLICT) {
        toast.error(`整批已暂停：${updated.conflict_detail.length} 条基线冲突，请核对三值`);
      } else if (updated.status === S.FAILED) {
        toast.error(`发布失败，旧价已保留，可重试（${updated.error || '未知原因'}）`);
      } else {
        toast.ok(`${updated.batch_no} ${label}`);
      }
    } catch (err) {
      toast.error(errorMessage((err as { payload?: unknown }).payload, '操作失败'));
    } finally {
      setBusy('');
    }
  }

  async function submit() {
    const parsed: CorrectionItemInput[] = [];
    for (const row of rows) {
      const listingId = Number(row.listing_id);
      const target = Number(row.target_price);
      if (!Number.isInteger(listingId) || listingId < 1 || !Number.isInteger(target) || target < 1000) {
        toast.error('每行都要填：房源 ID（正整数）与目标单价（≥1000）');
        return;
      }
      parsed.push({ listing_id: listingId, target_price: target });
    }
    if (note.trim().length < 2) {
      toast.error('批次备注至少 2 个字（写进审计）');
      return;
    }
    setBusy('submit');
    try {
      const batch = await correctionsApi.submit({ request_key: requestKey, note: note.trim(), items: parsed });
      toast.ok(`${batch.batch_no} 已提报（${batch.item_count} 条），等研究组审批`);
      setShowForm(false);
      setNote('');
      setRows([{ listing_id: '', target_price: '' }]);
      setRequestKey(newRequestKey());
      await correctionsStore.load();
      await correctionsStore.open(batch.id);
    } catch (err) {
      toast.error(errorMessage((err as { payload?: unknown }).payload, '提报失败'));
    } finally {
      setBusy('');
    }
  }

  const metrics = [
    { k: '批次总数', v: counts?.total ?? '—', unit: '批', d: '门店集中提报' },
    { k: '待审批', v: counts?.by_status.submitted ?? '—', unit: '批', d: '等研究组核对基线' },
    { k: '待发布', v: counts?.by_status.approved ?? '—', unit: '批', d: '审批通过待下发' },
    { k: '冲突暂停', v: counts?.by_status.conflict ?? '—', unit: '批', d: '当前价偏离提交基线' },
    { k: '发布失败', v: counts?.by_status.failed ?? '—', unit: '批', d: '旧价保留可重试' },
    { k: '已生效', v: counts?.by_status.applied ?? '—', unit: '批', d: '价格/行情/工单同批刷新' },
  ];

  const batchColumns = [
    {
      key: 'batch',
      label: '批次',
      render: (row: CorrectionBatchRow) => (
        <div>
          <div className="mono" style={{ fontWeight: 600 }}>{row.batch_no}</div>
          <div className="tiny muted">{row.note}</div>
        </div>
      ),
    },
    {
      key: 'status',
      label: '状态',
      render: (row: CorrectionBatchRow) => <StampTag kind={row.status} label={row.status_label} />,
    },
    {
      key: 'count',
      label: '条数',
      align: 'right' as const,
      render: (row: CorrectionBatchRow) => (
        <span className="mono">
          {row.status === S.APPLIED ? `${row.applied_count}/` : ''}{row.item_count}
        </span>
      ),
    },
    {
      key: 'who',
      label: '提报',
      render: (row: CorrectionBatchRow) => (
        <div className="tiny">
          <div>{row.submitted_by}</div>
          <div className="muted">{dateText(row.submitted_at)}</div>
        </div>
      ),
    },
  ];

  const conflictIds = new Set((current?.conflict_detail || []).map((entry) => entry.listing_id));

  return (
    <div className="stack fade-in">
      <MetricStrip items={metrics} />

      <div className="grid-2">
        <div className="panel">
          <div className="panel-head">
            <h2>修正批次</h2>
            <div className="row" style={{ gap: 6 }}>
              {canSubmit ? (
                <button
                  className="btn btn-sm"
                  onClick={() => setShowForm((value) => !value)}
                  data-testid="new-batch"
                >
                  {showForm ? '收起提报单' : '新建提报'}
                </button>
              ) : null}
            </div>
          </div>
          <div className="panel-body stack" style={{ gap: 12 }}>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <button
                className={`btn btn-sm ${status === '' ? 'btn-primary' : ''}`}
                onClick={() => correctionsStore.setStatus('')}
              >
                全部 {counts?.total ?? ''}
              </button>
              {CORRECTION_STATUS_ORDER.map((value) => (
                <button
                  key={value}
                  className={`btn btn-sm ${status === value ? 'btn-primary' : ''}`}
                  onClick={() => correctionsStore.setStatus(value)}
                >
                  {CORRECTION_STATUS_LABELS[value]} {counts?.by_status[value] ?? 0}
                </button>
              ))}
            </div>

            {error ? <ErrorState error={error} onRetry={load} title="批次列表没读出来" /> : null}

            {loading && items.length === 0 ? (
              <div className="empty"><span className="spinner" /> 正在调批次 …</div>
            ) : items.length === 0 ? (
              <EmptyState
                title="还没有修正批次"
                hint="门店集中提报挂牌价修正后，研究组在这里审批、统一下发；审批与发布都会按房源基线核对。"
              />
            ) : (
              <LedgerTable
                columns={batchColumns}
                rows={items}
                rowKey={(row) => row.id}
                onRowClick={(row) => open(row.id)}
                rowClassName={(row) => (current?.id === row.id ? 'active' : '')}
                testId="batch-ledger"
              />
            )}
          </div>
        </div>

        <div className="stack">
          {showForm && canSubmit ? (
            <div className="panel">
              <div className="panel-head">
                <h2>新建提报</h2>
                <span className="tiny muted mono">幂等键 {requestKey.slice(0, 18)}…</span>
              </div>
              <div className="panel-body stack" style={{ gap: 10 }}>
                <div className="tiny muted">
                  每行一套房源：提交时后端快照当前单价作为基线（原值）；审批与发布时若当前价偏离基线，整批暂停。
                </div>
                {rows.map((row, index) => (
                  <div className="row" key={index} style={{ gap: 6 }}>
                    <input
                      className="input"
                      style={{ width: 110 }}
                      placeholder="房源 ID"
                      value={row.listing_id}
                      onChange={(event) =>
                        setRows(rows.map((r, i) => (i === index ? { ...r, listing_id: event.target.value } : r)))
                      }
                    />
                    <input
                      className="input"
                      style={{ width: 140 }}
                      placeholder="目标单价 元/㎡"
                      value={row.target_price}
                      onChange={(event) =>
                        setRows(rows.map((r, i) => (i === index ? { ...r, target_price: event.target.value } : r)))
                      }
                    />
                    <button
                      className="btn btn-sm btn-ghost"
                      disabled={rows.length <= 1}
                      onClick={() => setRows(rows.filter((_, i) => i !== index))}
                    >
                      删行
                    </button>
                  </div>
                ))}
                <div className="row" style={{ gap: 6 }}>
                  <button
                    className="btn btn-sm"
                    disabled={rows.length >= 50}
                    onClick={() => setRows([...rows, { listing_id: '', target_price: '' }])}
                  >
                    加一行
                  </button>
                  <input
                    className="input"
                    style={{ flex: 1 }}
                    placeholder="批次备注（如：兰亭苑门店 11-14 回价）"
                    value={note}
                    onChange={(event) => setNote(event.target.value)}
                  />
                </div>
                <div className="row">
                  <button className="btn btn-primary btn-sm" disabled={busy === 'submit'} onClick={submit} data-testid="submit-batch">
                    {busy === 'submit' ? '提报中…' : `提报 ${rows.length} 条`}
                  </button>
                  <span className="tiny muted">重复点击 / 网络重发不会产生第二张单</span>
                </div>
              </div>
            </div>
          ) : null}

          {current ? (
            <div className="panel">
              <div className="panel-head">
                <h2>批次 {current.batch_no}</h2>
                <StampTag kind={current.status} label={current.status_label} fresh />
              </div>
              <div className="panel-body stack" style={{ gap: 10 }}>
                <div className="row spread tiny">
                  <span>提报 {current.submitted_by} · {dateText(current.submitted_at)}</span>
                  {current.reviewed_by ? <span>审批 {current.reviewed_by}</span> : null}
                  {current.published_by ? <span>发布 {current.published_by}</span> : null}
                </div>

                {current.status === S.CONFLICT && current.conflict_detail.length ? (
                  <div className="note-pin" data-testid="conflict-panel">
                    <div className="eyebrow" style={{ marginBottom: 4 }}>基线冲突 · 整批暂停</div>
                    {current.conflict_detail.map((entry) => (
                      <div key={entry.listing_id} className="tiny" style={{ marginTop: 4 }}>
                        房源 #{entry.listing_id}：原值 <b className="mono">{yuan(entry.base_price)}</b>
                        {' → '}提交值 <b className="mono">{yuan(entry.target_price)}</b>
                        {'，'}当前值 <b className="mono" style={{ color: 'var(--ochre)' }}>{yuan(entry.current_price)}</b>
                        <div className="muted">{entry.reason}</div>
                      </div>
                    ))}
                  </div>
                ) : null}

                {current.status === S.FAILED ? (
                  <div className="note-pin">
                    <div className="eyebrow" style={{ marginBottom: 4 }}>发布失败 · 旧价已保留</div>
                    <div className="tiny">{current.error || '未知原因'}；重试不会重复改价。</div>
                  </div>
                ) : null}

                <LedgerTable
                  columns={[
                    {
                      key: 'house',
                      label: '房源',
                      render: (row) => (
                        <div>
                          <div style={{ fontWeight: 600 }}>
                            #{row.listing_id} {row.community || '房源已删除'} {row.layout || ''}
                          </div>
                          <div className="tiny muted">{row.source_name || '—'}</div>
                        </div>
                      ),
                    },
                    {
                      key: 'base',
                      label: '原值',
                      align: 'right' as const,
                      render: (row) => <span className="mono">{yuan(row.base_price)}</span>,
                    },
                    {
                      key: 'target',
                      label: '提交值',
                      align: 'right' as const,
                      render: (row) => <span className="mono" style={{ fontWeight: 600 }}>{yuan(row.target_price)}</span>,
                    },
                    {
                      key: 'current',
                      label: '当前值',
                      align: 'right' as const,
                      render: (row) => (
                        <span
                          className="mono"
                          style={conflictIds.has(row.listing_id) ? { color: 'var(--ochre)', fontWeight: 700 } : undefined}
                        >
                          {yuan(row.current_price)}
                        </span>
                      ),
                    },
                    {
                      key: 'state',
                      label: '状态',
                      align: 'right' as const,
                      render: (row) => <StampTag kind={row.status} label={row.status_label} />,
                    },
                  ]}
                  rows={current.items}
                  rowKey={(row) => row.id}
                  caption="三值口径：原值＝提报时基线 · 提交值＝门店要改的价 · 当前值＝实时单价"
                  testId="batch-items"
                />

                {current.versions.length ? (
                  <div>
                    <div className="eyebrow" style={{ margin: '4px 0 6px' }}>价格版本（只增不删）</div>
                    {current.versions.map((version) => (
                      <div key={version.id} className="tiny row spread" style={{ padding: '2px 0' }}>
                        <span>
                          #{version.listing_id} {version.source_label}：
                          <span className="mono"> {yuan(version.old_price)} → {yuan(version.new_price)}</span>
                        </span>
                        <span className="muted">{version.actor_email} · {dateText(version.created_at)}</span>
                      </div>
                    ))}
                  </div>
                ) : null}

                {canApprove ? (
                  <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                    {current.status === S.SUBMITTED ? (
                      <button className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => act('approve', current)} data-testid="approve-batch">
                        审批（核对基线）
                      </button>
                    ) : null}
                    {current.status === S.APPROVED ? (
                      <button className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => act('publish', current)} data-testid="publish-batch">
                        发布（原子生效）
                      </button>
                    ) : null}
                    {current.status === S.CONFLICT ? (
                      <button className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => act('rebase', current)} data-testid="rebase-batch">
                        以当前价重定基线
                      </button>
                    ) : null}
                    {current.status === S.FAILED ? (
                      <button className="btn btn-primary btn-sm" disabled={!!busy} onClick={() => act('retry', current)} data-testid="retry-batch">
                        重试发布
                      </button>
                    ) : null}
                    {current.status !== S.WITHDRAWN ? (
                      <button className="btn btn-sm" disabled={!!busy} onClick={() => act('withdraw', current)} data-testid="withdraw-batch">
                        撤回批次
                      </button>
                    ) : null}
                  </div>
                ) : (
                  <div className="tiny muted">审批 / 发布 / 撤回仅研究组长可操作（后端按角色 403）。</div>
                )}
              </div>
            </div>
          ) : (
            <EmptyState title="左侧选一个批次" hint="这里会列出每条修正的原值 / 提交值 / 当前值，以及审批、发布、撤回动作。" />
          )}

          <div className="note-pin">
            <div className="eyebrow" style={{ marginBottom: 4 }}>一致性口径</div>
            审批与发布两道闸口都按房源基线核对；任一冲突整批暂停并列出三值。无冲突时改价、价格版本、
            去重行情与虚高工单在同一事务生效；发布失败整体回滚保留旧价；撤回只追加反向版本，
            被后续采集改过的房源跳过不覆盖。
          </div>
        </div>
      </div>
    </div>
  );
}
