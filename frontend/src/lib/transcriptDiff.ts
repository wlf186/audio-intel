export type TextChange={text:string;changed:boolean}
// Bounded per-paragraph LCS; no HTML insertion and no dependency on model markup.
export function transcriptDiff(original:string,edited:string):{original:TextChange[];edited:TextChange[]}{
 const a=Array.from(original),b=Array.from(edited)
 if(a.length*b.length>2_000_000)return{original:[{text:original,changed:original!==edited}],edited:[{text:edited,changed:original!==edited}]}
 const cols=b.length+1,grid=new Uint16Array((a.length+1)*cols)
 for(let i=a.length-1;i>=0;i--)for(let j=b.length-1;j>=0;j--)grid[i*cols+j]=a[i]===b[j]?1+grid[(i+1)*cols+j+1]:Math.max(grid[(i+1)*cols+j],grid[i*cols+j+1])
 const left:TextChange[]=[],right:TextChange[]=[]
 const append=(items:TextChange[],text:string,changed:boolean)=>{const last=items.at(-1);if(last?.changed===changed)last.text+=text;else items.push({text,changed})}
 let i=0,j=0
 while(i<a.length||j<b.length){
  if(i<a.length&&j<b.length&&a[i]===b[j]){append(left,a[i++],false);append(right,b[j++],false)}
  else if(i<a.length&&(j===b.length||grid[(i+1)*cols+j]>=grid[i*cols+j+1]))append(left,a[i++],true)
  else append(right,b[j++],true)
 }
 return{original:left,edited:right}
}
