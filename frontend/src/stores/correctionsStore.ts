/** 挂牌价修正批次：列表筛选 + 当前打开批次，动作后整单刷新（服务端是真相）。 */
import type { CorrectionBatchPage, CorrectionBatchRow, CorrectionCounts } from '../types/models';
import { correctionsApi } from '../api/corrections';

type Listener = () => void;

class CorrectionsStore {
  private status = '';
  private items: CorrectionBatchRow[] = [];
  private counts: CorrectionCounts | null = null;
  private current: CorrectionBatchRow | null = null;
  private listeners = new Set<Listener>();

  subscribe = (listener: Listener) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getStatus = (): string => this.status;

  getItems = (): CorrectionBatchRow[] => this.items;

  getCounts = (): CorrectionCounts | null => this.counts;

  getCurrent = (): CorrectionBatchRow | null => this.current;

  private emit() {
    this.listeners.forEach((listener) => listener());
  }

  setStatus(status: string) {
    this.status = status;
    this.emit();
  }

  async load(): Promise<CorrectionBatchPage> {
    const result = await correctionsApi.list({ status: this.status || undefined });
    this.items = result.items;
    this.counts = result.counts;
    this.emit();
    return result;
  }

  async open(batchId: number): Promise<CorrectionBatchRow> {
    this.current = await correctionsApi.detail(batchId);
    this.emit();
    return this.current;
  }

  /** 动作（审批/发布/撤回…）返回的最新批次：同步详情与列表行。 */
  applyBatch(batch: CorrectionBatchRow) {
    this.current = batch;
    this.items = this.items.map((item) => (item.id === batch.id ? { ...item, ...batch } : item));
    this.emit();
  }
}

export const correctionsStore = new CorrectionsStore();
