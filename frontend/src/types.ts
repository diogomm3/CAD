export type DownloadableFile = { name:string; extension:string; category:string; url:string|null; downloadable:boolean; reason?:string|null }
export type Model = { id:string; source:string; title:string; author:string|null; model_url:string; thumbnail_url:string|null; image_urls:string[]; downloads:number|null; likes:number|null; favorites:number|null; rating:number|null; popularity_value:number|null; popularity_label:string|null; available_files:DownloadableFile[]; description:string|null; license:string|null }
export type SourceResult = { source:string; label:string; status:'success'|'error'; results:Model[]; error:string|null }
export type SearchResponse = { query:string; sources:SourceResult[]; total_results:number }
