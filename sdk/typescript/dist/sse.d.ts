import type { SseEvent } from './types.js';
export declare function parseSseFrame(frame: string): SseEvent | null;
export declare function readSseEvents(response: Response): AsyncGenerator<SseEvent>;
