#!/usr/bin/env python3
import argparse, json, pathlib, re, whisper

def t(s):
    s=max(0,float(s)); h=int(s//3600); m=int((s%3600)//60); x=s%60
    return f"{h}:{m:02d}:{x:05.2f}"
def clean(x): return re.sub(r"\s+"," ",x or "").strip()
def esc(x): return clean(x).replace("{",r"\{").replace("}",r"\}")
def chunks(words):
    out=[]; buf=[]; start=end=None
    for w in words:
        token=clean(w.get("word",""))
        if not token: continue
        proposed=" ".join(buf+[token])
        if buf and (len(buf)>=4 or len(proposed)>28):
            out.append((start,end," ".join(buf))); buf=[]; start=end=None
        if start is None: start=float(w.get("start",0))
        end=float(w.get("end",start+0.5)); buf.append(token)
        if token.endswith((".","?","!",",",":",";")) and len(buf)>=2:
            out.append((start,end," ".join(buf))); buf=[]; start=end=None
    if buf: out.append((start or 0,end or (start or 0)+1," ".join(buf)))
    return out

def main():
    p=argparse.ArgumentParser(); p.add_argument("--audio",required=True); p.add_argument("--manifest",required=True); p.add_argument("--out",required=True); p.add_argument("--transcript",required=True); p.add_argument("--model",default="base.en"); a=p.parse_args()
    manifest=json.load(open(a.manifest,encoding="utf-8")); model=whisper.load_model(a.model); r=model.transcribe(a.audio,language="en",word_timestamps=True,fp16=False)
    words=[]
    for s in r.get("segments",[]): words.extend(s.get("words") or [])
    caps=chunks(words)
    pathlib.Path(a.transcript).write_text(clean(r.get("text",""))+"\n",encoding="utf-8")
    header="""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Caption,DejaVu Sans,60,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,10,0,2,95,95,650,1
Style: Overlay,DejaVu Sans,70,&H00000000,&H000000FF,&H00000000,&H0000FFFF,-1,0,0,0,100,100,0,0,3,14,2,8,105,105,215,1

[Events]
Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
"""
    ev=[]
    for s,e,x in caps: ev.append(f"Dialogue: 0,{t(s)},{t(max(e,s+0.35))},Caption,,0,0,0,,{esc(x.upper())}")
    for o in manifest.get("overlays",[]): ev.append(f"Dialogue: 1,{t(o['start'])},{t(o['end'])},Overlay,,0,0,0,,{esc(o['text'].upper())}")
    pathlib.Path(a.out).write_text(header+"\n".join(ev)+"\n",encoding="utf-8")
    print(json.dumps({"caption_chunks":len(caps),"overlays":len(manifest.get("overlays",[])),"reference_style":"HE-01"}))
if __name__=="__main__": main()
