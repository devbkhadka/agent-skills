export interface SearchRequest {
  query: string;
  limit: number;
}

export function buildSearchRequest(rawQuery: string): SearchRequest {
  return {
    query: rawQuery.trim(),
    limit: 20
  };
}
