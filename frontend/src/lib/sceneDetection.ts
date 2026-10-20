export type SceneDetectionSettings={min_change_percent:number;min_stable_seconds:number}
type Limit={min:number;max:number;step:number;default:number}
export type SceneDetectionCapability={detector:string;preview:boolean;min_change_percent:Limit;min_stable_seconds:Limit}
const key='audio-intel:video-scene-detection:v1'
export function detectionDefaults(capability:SceneDetectionCapability):SceneDetectionSettings{
 return {min_change_percent:capability.min_change_percent.default,min_stable_seconds:capability.min_stable_seconds.default}
}
export function validDetection(value:SceneDetectionSettings,capability:SceneDetectionCapability){
 return (['min_change_percent','min_stable_seconds'] as const).every(key=>Number.isFinite(value[key])&&value[key]>=capability[key].min&&value[key]<=capability[key].max)
}
export function loadDetection(capability:SceneDetectionCapability):SceneDetectionSettings{
 try{const saved=JSON.parse(localStorage.getItem(key)||'null');if(saved&&validDetection(saved,capability))return {min_change_percent:saved.min_change_percent,min_stable_seconds:saved.min_stable_seconds}}catch{/* Defaults also work with storage disabled. */}
 return detectionDefaults(capability)
}
export function saveDetection(value:SceneDetectionSettings){try{localStorage.setItem(key,JSON.stringify(value))}catch{/* Applying a result does not depend on browser storage. */}}
