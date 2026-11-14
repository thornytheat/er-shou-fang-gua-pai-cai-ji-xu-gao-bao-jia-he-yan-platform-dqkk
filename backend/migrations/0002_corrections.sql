-- 0002 挂牌价修正批次（MySQL 8.0）
-- 运行时同样由 Base.metadata.create_all 建表；此文件供迁移流程 / 索引预审。
-- 语义：
--   correction_batches.request_key  幂等键，重复提报命中唯一索引直接返回原批次
--   correction_items(batch_id, listing_id)  同批次同房源唯一，重复条目 422
--   listing_price_versions  只增不删：修正 / 手工 / 采集 / 撤回四类来源

CREATE TABLE IF NOT EXISTS correction_batches (
  id             INT AUTO_INCREMENT PRIMARY KEY,
  batch_no       VARCHAR(32)  NOT NULL,
  request_key    VARCHAR(80)  NOT NULL,
  status         VARCHAR(16)  NOT NULL DEFAULT 'submitted',
  item_count     INT NOT NULL DEFAULT 0,
  applied_count  INT NOT NULL DEFAULT 0,
  note           VARCHAR(200) NOT NULL DEFAULT '',
  submitted_by   VARCHAR(160) NOT NULL,
  submitted_at   DATETIME NOT NULL,
  reviewed_by    VARCHAR(160) NULL,
  reviewed_at    DATETIME NULL,
  published_by   VARCHAR(160) NULL,
  published_at   DATETIME NULL,
  conflict_detail JSON NULL,
  error          TEXT NULL,
  created_at     DATETIME NOT NULL,
  updated_at     DATETIME NOT NULL,
  UNIQUE KEY uq_correction_batches_no (batch_no),
  UNIQUE KEY uq_correction_batches_request_key (request_key),
  KEY ix_correction_batches_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS correction_items (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  batch_id     INT NOT NULL,
  listing_id   INT NOT NULL,
  base_price   INT NULL,
  target_price INT NOT NULL,
  status       VARCHAR(16) NOT NULL DEFAULT 'pending',
  applied_at   DATETIME NULL,
  created_at   DATETIME NOT NULL,
  updated_at   DATETIME NOT NULL,
  UNIQUE KEY uq_correction_item_batch_listing (batch_id, listing_id),
  KEY ix_correction_items_batch (batch_id),
  KEY ix_correction_items_listing (listing_id),
  CONSTRAINT fk_correction_item_batch FOREIGN KEY (batch_id) REFERENCES correction_batches (id),
  CONSTRAINT fk_correction_item_listing FOREIGN KEY (listing_id) REFERENCES listings (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS listing_price_versions (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  listing_id  INT NOT NULL,
  batch_id    INT NULL,
  source      VARCHAR(16) NOT NULL,
  old_price   INT NULL,
  new_price   INT NULL,
  actor_email VARCHAR(160) NOT NULL DEFAULT 'system',
  created_at  DATETIME NOT NULL,
  KEY ix_price_versions_listing (listing_id, id),
  KEY ix_price_versions_batch (batch_id),
  CONSTRAINT fk_price_versions_listing FOREIGN KEY (listing_id) REFERENCES listings (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
