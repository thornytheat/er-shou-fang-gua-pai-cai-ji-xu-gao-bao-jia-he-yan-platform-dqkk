import { http, query } from './client';
import type { CorrectionBatchPage, CorrectionBatchRow } from '../types/models';

export interface CorrectionItemInput {
  listing_id: number;
  target_price: number;
}

export const correctionsApi = {
  list: (params: { status?: string }) => http.get<CorrectionBatchPage>(`/corrections${query(params)}`),
  detail: (batchId: number) => http.get<CorrectionBatchRow>(`/corrections/${batchId}`),
  submit: (payload: { request_key: string; note: string; items: CorrectionItemInput[] }) =>
    http.post<CorrectionBatchRow>('/corrections', payload),
  approve: (batchId: number) => http.post<CorrectionBatchRow>(`/corrections/${batchId}/approve`),
  publish: (batchId: number) => http.post<CorrectionBatchRow>(`/corrections/${batchId}/publish`),
  retry: (batchId: number) => http.post<CorrectionBatchRow>(`/corrections/${batchId}/retry`),
  rebase: (batchId: number) => http.post<CorrectionBatchRow>(`/corrections/${batchId}/rebase`),
  withdraw: (batchId: number) => http.post<CorrectionBatchRow>(`/corrections/${batchId}/withdraw`),
};
