export type StreamEvent = {
    type: string;
    data: unknown;
};
export type SseEvent = {
    id?: number;
    event?: string;
    data?: unknown;
};
export type RetrievalPolicy = {
    allowed_origins: Array<'public_web' | 'private_corpus' | 'user_provided' | 'external_system'>;
    channels: Array<'search_api' | 'browser' | 'crawler' | 'file_upload' | 'mcp' | 'native_connector'>;
    methods: Array<'web_search' | 'academic_search' | 'vector_search' | 'keyword_search' | 'crawl' | 'deep_read' | 'mcp_search' | 'mcp_fetch'>;
    profiles?: Array<'general' | 'academic' | 'news' | 'social' | 'code' | 'finance' | 'legal'>;
    domain_policy?: Record<string, unknown>;
    corpus_policy?: Record<string, unknown>;
    connector_policy?: Record<string, unknown>;
    budget?: Record<string, unknown>;
};
