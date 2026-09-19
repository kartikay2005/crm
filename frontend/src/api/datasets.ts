import { apiClient } from "./client";
import type {
  DatasetListResponse, DatasetUploadResponse, DomainInfo, InsightBundle,
  ModelInfo, PredictionDetail, Recommendation, Chart,
} from "./types";

export async function uploadDataset(file: File): Promise<DatasetUploadResponse> {
  const form = new FormData();
  form.append("file", file);
  const { data } = await apiClient.post<DatasetUploadResponse>("/datasets/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export async function listDatasets(limit = 50, offset = 0): Promise<DatasetListResponse> {
  const { data } = await apiClient.get<DatasetListResponse>("/datasets", { params: { limit, offset } });
  return data;
}

export async function getProfile(datasetId: string) {
  const { data } = await apiClient.get(`/datasets/${datasetId}/profile`);
  return data;
}

export async function getAnalysis(datasetId: string) {
  const { data } = await apiClient.get(`/datasets/${datasetId}/analysis`);
  return data;
}

export async function getPredictions(datasetId: string): Promise<PredictionDetail> {
  const { data } = await apiClient.get<PredictionDetail>(`/datasets/${datasetId}/predictions`);
  return data;
}

export async function getInsights(datasetId: string): Promise<InsightBundle> {
  const { data } = await apiClient.get<InsightBundle>(`/datasets/${datasetId}/insights`);
  return data;
}

export async function getRecommendations(datasetId: string): Promise<Recommendation[]> {
  const { data } = await apiClient.get<Recommendation[]>(`/datasets/${datasetId}/recommendations`);
  return data;
}

export async function getCharts(datasetId: string): Promise<Chart[]> {
  const { data } = await apiClient.get<Chart[]>(`/datasets/${datasetId}/charts`);
  return data;
}

export async function downloadReport(datasetId: string, format: "pdf" | "excel" | "csv" | "json"): Promise<void> {
  const { data, headers } = await apiClient.get(`/datasets/${datasetId}/report`, {
    params: { format }, responseType: "blob",
  });
  const disposition = headers["content-disposition"] as string | undefined;
  const match = disposition?.match(/filename="(.+)"/);
  const filename = match?.[1] ?? `report.${format}`;

  const url = URL.createObjectURL(data as Blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export async function repredict(datasetId: string) {
  const { data } = await apiClient.post(`/datasets/${datasetId}/predict`);
  return data;
}

export async function deleteDataset(datasetId: string): Promise<void> {
  await apiClient.delete(`/datasets/${datasetId}`);
}

export async function listModels(): Promise<ModelInfo[]> {
  const { data } = await apiClient.get<ModelInfo[]>("/models");
  return data;
}

export async function listDomains(): Promise<DomainInfo[]> {
  const { data } = await apiClient.get<DomainInfo[]>("/models/domains");
  return data;
}
