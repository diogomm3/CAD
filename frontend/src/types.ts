export type DownloadableFile = { name:string; extension:string; category:string; url:string|null; downloadable:boolean; reason?:string|null }
export type Model = { id:string; source:string; title:string; author:string|null; model_url:string; thumbnail_url:string|null; image_urls:string[]; downloads:number|null; likes:number|null; favorites:number|null; rating:number|null; popularity_value:number|null; popularity_label:string|null; available_files:DownloadableFile[]; description:string|null; license:string|null }
export type SourceStatus = 'ready'|'success'|'success_empty'|'blocked'|'authentication_required'|'api_key_required'|'rate_limited'|'parse_error'|'network_error'|'timeout'|'unsupported'
export type SourceResult = { source:string; label:string; status:SourceStatus; search_method?:string; diagnostics?:Record<string,unknown>; results:Model[]; error:{code:string;message:string}|null }
export type SearchResponse = { query:string; sources:SourceResult[]; total_results:number }
export type SourceAvailability = { id:string;name:string;enabled:boolean;access_mode:string;search_method:string;status:SourceStatus;message:string|null;last_check:string|null }
