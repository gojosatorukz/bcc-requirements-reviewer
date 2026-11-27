import json, math, subprocess, wave, sys, random
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import imageio_ffmpeg
FF = imageio_ffmpeg.get_ffmpeg_exe()
SRC = "/Users/rashidulybekzat/Desktop/финтех хакатон/submission/demo.mp4"
FONT = "/Users/rashidulybekzat/Desktop/финтех хакатон/reviewer/fonts/Rubik.ttf"
OUT = sys.argv[1]
FPS = 24
seg = json.load(open("segments.json"))
ORANGE=(255,97,47); YELLOW=(255,225,72); INK=(25,22,29); PAPER=(255,252,244); SMILE=(255,229,157)
def font(size, w="Bold"):
    f = ImageFont.truetype(FONT, size); f.set_variation_by_name(w); return f

# 1) background
bg = Image.new("RGB", (1920,1080), ORANGE); d = ImageDraw.Draw(bg)
d.ellipse((1280,560,2280,1560), fill=YELLOW)
d.rounded_rectangle((56,72,56+1368,72+968), 18, fill=INK)          # hard shadow
d.rounded_rectangle((44,56,44+1368,56+968), 18, fill=INK)          # border
bg.save("bg.png")

# 2) audio track
inp=[]; flt=[]
for i,s in enumerate(seg):
    inp += ["-i", f"c{i}.wav"]; flt.append(f"[{i}:a]apad=whole_dur={s['D']:.3f}[a{i}]")
flt.append("".join(f"[a{i}]" for i in range(len(seg))) + f"concat=n={len(seg)}:v=0:a=1,loudnorm=I=-16:TP=-1.5[a]")
subprocess.run([FF,"-y","-v","error",*inp,"-filter_complex",";".join(flt),"-map","[a]","-ar","48000","-ac","1","voice.wav"],check=True)

# 3) retimed product video on background
parts=[]
for i,s in enumerate(seg):
    dur=s["end"]-s["start"]; k=min(1.35, s["D"]/dur)
    parts.append(f"[0:v]trim=start={s['start']}:end={s['end']},setpts=(PTS-STARTPTS)*{k:.4f},fps={FPS},"
                 f"tpad=stop_mode=clone:stop_duration={s['D']:.3f},trim=duration={s['D']:.3f},setpts=PTS-STARTPTS[v{i}]")
parts.append("".join(f"[v{i}]" for i in range(len(seg))) + f"concat=n={len(seg)}:v=1:a=0[vc]")
parts.append("[1:v][vc]overlay=48:60:shortest=1,format=yuv420p[vo]")
subprocess.run([FF,"-y","-v","error","-i",SRC,"-loop","1","-framerate",str(FPS),"-i","bg.png","-filter_complex",";".join(parts),
                "-map","[vo]","-r",str(FPS),"-c:v","libx264","-crf","14","-preset","fast","base.mp4"],check=True)

# 4) per-frame loudness
w = wave.open("voice.wav"); sr=w.getframerate(); a=np.frombuffer(w.readframes(w.getnframes()),dtype=np.int16).astype(np.float32)/32768
total = sum(s["D"] for s in seg); N=int(total*FPS)
hop=sr//FPS; rms=np.array([np.sqrt(np.mean(a[i*hop:(i+1)*hop]**2)) if i*hop<len(a) else 0 for i in range(N)])
rms=rms/ (np.percentile(rms[rms>0],95) if (rms>0).any() else 1); rms=np.clip(rms,0,1.2)
sm=np.convolve(rms,np.ones(2)/2,mode="same")
starts=np.cumsum([0]+[s["D"] for s in seg])

# 5) panel frames
PW,PH=490,1080
f_text=font(27,"Medium"); f_title=font(22,"Bold"); f_brand=font(26,"ExtraBold"); f_small=font(18,"SemiBold")
def wrap(text, f, width):
    words=text.split(); lines=[]; cur=""
    for wd in words:
        t=(cur+" "+wd).strip()
        if f.getlength(t)<=width: cur=t
        else: lines.append(cur); cur=wd
    lines.append(cur); return lines
random.seed(4)
sparks=[[ (random.uniform(0,2*math.pi), random.uniform(120,220)) for _ in range(7)] for _ in seg]
CH="char"
SPR={}
for m_ in ("smile","small","open"):
    for e_ in ("open","closed"):
        im_=Image.open(f"{CH}/girl-{m_}-{e_}.png").convert("RGBA"); im_=im_.resize((int(im_.width*0.62),int(im_.height*0.62)),Image.LANCZOS); SPR[(m_,e_)]=im_
def mascot(t, amp, si, ts):
    pop = 1+0.06*math.exp(-ts*5)*math.cos(ts*14) if ts<1.2 else 1
    blink = (t % 3.7) < 0.13
    mouth = "smile" if amp<0.12 else ("small" if amp<0.5 else "open")
    im = SPR[(mouth, "closed" if blink else "open")]
    if pop!=1: im = im.resize((int(im.width*pop),int(im.height*pop)),Image.BICUBIC)
    ang = math.sin(t*2*math.pi*0.35)*2.5
    return im.rotate(ang, resample=Image.BICUBIC, expand=True)
proc=subprocess.Popen([FF,"-y","-v","error","-i","base.mp4","-f","rawvideo","-pix_fmt","rgba","-s",f"{PW}x{PH}","-r",str(FPS),"-i","-",
    "-i","voice.wav","-filter_complex","[0:v][1:v]overlay=1430:0:shortest=1,format=yuv420p[v]","-map","[v]","-map","2:a",
    "-c:v","libx264","-crf","17","-preset","medium","-c:a","aac","-b:a","160k","-movflags","+faststart","-shortest",OUT],stdin=subprocess.PIPE)
for n in range(N):
    t=n/FPS; si=int(np.searchsorted(starts,t,side="right")-1); si=min(si,len(seg)-1); ts=t-starts[si]
    p=Image.new("RGBA",(PW,PH),(0,0,0,0)); d=ImageDraw.Draw(p)
    # brand
    d.rounded_rectangle((20,40,58,78),10,fill=INK); d.text((39,59),"#",font=f_brand,fill=ORANGE,anchor="mm")
    d.text((72,59),"Разбор ТЗ",font=f_brand,fill=INK,anchor="lm")
    # scene pill
    title=seg[si]["screen"]; tw=f_title.getlength(title)
    lines_t=wrap(title,f_title,420)
    yy=110
    for ln in lines_t:
        tw=f_title.getlength(ln); d.rounded_rectangle((20,yy,20+tw+28,yy+38),19,fill=INK); d.text((34,yy+19),ln,font=f_title,fill=YELLOW,anchor="lm"); yy+=46
    # bubble
    lines=wrap(seg[si]["text"],f_text,410)
    shown=min(len(lines), 1+int(ts/ (seg[si]['L']/max(1,len(lines))) )) if ts<seg[si]['L'] else len(lines)
    bh=36*len(lines)+44; by=yy+18
    d.rounded_rectangle((26,by+7,26+446,by+7+bh),22,fill=INK)
    d.rounded_rectangle((20,by,20+446,by+bh),22,fill=PAPER,outline=INK,width=3)
    tail_y=by+bh
    d.polygon([(330,tail_y-2),(380,tail_y-2),(370,tail_y+30)],fill=PAPER,outline=None)
    d.line([(330,tail_y),(370,tail_y+30),(380,tail_y)],fill=INK,width=3)
    for k,ln in enumerate(lines[:shown]):
        d.text((42,by+24+36*k),ln,font=f_text,fill=INK)
    # mascot
    amp=float(sm[n]) if n<len(sm) else 0
    m=mascot(t,amp,si,ts); bob=math.sin(t*2*math.pi*0.7)*6; mx,my=(PW-m.width)/2+10, PH-m.height-48+bob
    p.alpha_composite(m,(int(mx),int(my)))
    # sparks on scene change
    if ts<0.9:
        for ang,dist in sparks[si]:
            r=dist*min(1,ts/0.6); x=mx+m.width/2+math.cos(ang)*r; y=my+m.height/2+math.sin(ang)*r*0.8
            a_=int(255*max(0,1-ts/0.9)); s_=10*(1-ts/0.9)+4
            d2=ImageDraw.Draw(p); d2.polygon([(x,y-s_),(x+s_*0.35,y-s_*0.35),(x+s_,y),(x+s_*0.35,y+s_*0.35),(x,y+s_),(x-s_*0.35,y+s_*0.35),(x-s_,y),(x-s_*0.35,y-s_*0.35)],fill=(255,252,244,a_))
    # progress
    d.rounded_rectangle((20,PH-40,PW-20,PH-28),6,fill=(25,22,29,90)); d.rounded_rectangle((20,PH-40,20+(PW-40)*t/total,PH-28),6,fill=INK)
    proc.stdin.write(p.tobytes())
proc.stdin.close(); proc.wait(); print("done", round(total,1), "s")
