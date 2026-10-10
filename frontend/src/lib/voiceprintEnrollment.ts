import type {Speaker,VoiceprintPerson} from './types'
import {speakerIdentity} from './presentation'

const key=(value:string)=>value.normalize('NFKC').trim().replace(/\s+/g,' ').toLocaleLowerCase()
export function enrollmentPerson(speaker:Speaker|undefined,label:string,people:VoiceprintPerson[]){
 const identity=speakerIdentity(speaker,label)
 const matchedId=speaker?.label_source==='voiceprint'?speaker.voiceprint_match?.person_id:undefined
 const candidates=people.filter(person=>key(person.name)===key(identity.name))
 const match=people.find(person=>person.id===matchedId)||candidates.find(person=>key(person.note||'')===key(identity.note))
 return {identity,match,ambiguous:!match&&candidates.length>1}
}
