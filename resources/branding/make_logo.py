"""Generate app logo/icon: scallop shell + camera focus frame on teal gradient.
Run: python resources/branding/make_logo.py
"""
from PIL import Image, ImageDraw
import math, os

S=1024; SS=4; W=S*SS
ACCENT_TOP=(20,184,166); ACCENT_BOT=(15,118,110); INK=(13,107,99)
WHITE=(255,255,255,255)

def lerp(a,b,t): return tuple(round(a[i]+(b[i]-a[i])*t) for i in range(3))
def gradient(w,h,top,bot):
    g=Image.new("RGB",(w,h)); px=g.load()
    for y in range(h):
        c=lerp(top,bot,y/(h-1))
        for x in range(w): px[x,y]=c
    return g
def rmask(w,h,r):
    m=Image.new("L",(w,h),0); ImageDraw.Draw(m).rounded_rectangle([0,0,w-1,h-1],radius=r,fill=255); return m

img=Image.new("RGBA",(W,W),(0,0,0,0))
grad=gradient(W,W,ACCENT_TOP,ACCENT_BOT).convert("RGBA"); grad.putalpha(rmask(W,W,int(W*0.22)))
img.alpha_composite(grad)
d=ImageDraw.Draw(img)
cx=W/2

# ---- scallop shell (fan-up, hinge at bottom) ----
R=W*0.30
hx,hy=cx, W*0.66          # hinge point
# fan body: upper half disk
shell=Image.new("RGBA",(W,W),(0,0,0,0)); sd=ImageDraw.Draw(shell)
sd.pieslice([hx-R,hy-R,hx+R,hy+R],start=180,end=360,fill=WHITE)
# scalloped top rim: bumps along arc
bumps=9
for k in range(bumps+1):
    a=math.radians(180+180*k/bumps)
    bx,by=hx+R*math.cos(a),hy+R*math.sin(a)
    br=R*0.085
    sd.ellipse([bx-br,by-br,bx+br,by+br],fill=WHITE)
# trim anything below hinge line (keep fan-up clean)
sd.rectangle([0,hy,W,W],fill=(0,0,0,0))
img.alpha_composite(shell)
d=ImageDraw.Draw(img)
# ribs (teal) radiating from hinge
ribs=9
for k in range(ribs):
    a=math.radians(180+180*(k+0.5)/ribs)
    d.line([(hx,hy),(hx+R*0.93*math.cos(a),hy+R*0.93*math.sin(a))],fill=INK+(255,),width=int(W*0.0075))
# hinge ears
ear=R*0.17
for s in (-1,1):
    ex=hx+s*R*0.0
# small base nub
d.ellipse([hx-R*0.13,hy-R*0.06,hx+R*0.13,hy+R*0.20],fill=WHITE)

# ---- camera focus-frame corner brackets ----
m=W*0.135; L=W*0.085; t=int(W*0.018)
corners=[(m,m,1,1),(W-m,m,-1,1),(m,W-m,1,-1),(W-m,W-m,-1,-1)]
for x,y,sx,sy in corners:
    d.line([(x,y),(x+sx*L,y)],fill=(255,255,255,235),width=t)
    d.line([(x,y),(x,y+sy*L)],fill=(255,255,255,235),width=t)

final=img.resize((S,S),Image.LANCZOS)
out=os.path.dirname(__file__)
final.save(os.path.join(out,"logo.png"))
for sz in (256,128,64,32,16):
    final.resize((sz,sz),Image.LANCZOS).save(os.path.join(out,f"app_{sz}.png"))
final.resize((256,256),Image.LANCZOS).save(os.path.join(out,"app.ico"),sizes=[(256,256),(128,128),(64,64),(32,32),(16,16)])
print("done")
