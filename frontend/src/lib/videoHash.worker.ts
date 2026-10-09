import {sha256} from '@noble/hashes/sha2.js'
self.onmessage=(event:MessageEvent<ArrayBuffer>)=>{
 const digest=sha256(new Uint8Array(event.data))
 self.postMessage(Array.from(digest,byte=>byte.toString(16).padStart(2,'0')).join(''))
}
