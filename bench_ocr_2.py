# -*- coding: utf-8 -*-
"""
bench_ocr_2.py — 补充闭合两个分支（只读）：
 (1) 难图分支：亮背景冲淡白字，使 gray/sharp 提前退出失败，看循环走到第几变体、
     white 是否真被执行、该路径端到端耗时与选中结果。
 (2) 懒求值候选 A/B：把 white_mask 从“无条件预计算(eager=现状)”改为“走到 white 才算(lazy)”，
     在 gray 直接命中的常见路径上配对测端到端，验证省掉预计算、且最终文本不变(oracle)。
"""
import os, sys, time, importlib.util, statistics
import numpy as np, cv2
ROOT = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("skyloop", os.path.join(ROOT, "sky-loop-v7.py"))
M = importlib.util.module_from_spec(spec); spec.loader.exec_module(M)


def base_image(hard=False):
    from PIL import Image, ImageDraw, ImageFont
    W,H=760,420
    if hard:  # 整体高亮、近白背景，模拟云野强光把白字冲淡
        img=Image.new("RGB",(W,H),(232,236,240)); dr=ImageDraw.Draw(img,"RGBA")
        dr.ellipse([480,20,760,240],fill=(248,246,238,255))
        dr.rounded_rectangle([20,60,520,360],radius=18,fill=(214,220,228,180))  # 很浅的半透明条
        stroke=(150,150,160,255)  # 浅描边
    else:
        img=Image.new("RGB",(W,H)); px=img.load()
        for y in range(H):
            for x in range(0,W,4):
                c=(30+x//60,45+y//40,40)
                for k in range(4):
                    if x+k<W: px[x+k,y]=c
        dr=ImageDraw.Draw(img,"RGBA")
        dr.rounded_rectangle([20,60,520,360],radius=18,fill=(20,22,30,120)); stroke=(55,55,65,255)
    font=ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc",30)
    for i,t in enumerate(["珂珂-你在哪里呀","星河-我在云野等你","珂珂-一起去暴风眼吗"]):
        dr.text((45,95+i*80),t,font=font,fill=(255,255,255,255),stroke_width=2,stroke_fill=stroke)
    return cv2.cvtColor(np.array(img),cv2.COLOR_RGB2BGR)


def run(frame, engine, lazy=False, trace=False):
    """等价 read_chat_ocr，lazy 控制 white_mask 是否懒求值；返回(items, 调用序列, 耗时ms)。"""
    h,w=frame.shape[:2]
    img=cv2.resize(frame,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
    gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
    eq=cv2.createCLAHE(3.0,(8,8)).apply(gray)
    sharp=cv2.filter2D(eq,-1,np.array([[-1,-1,-1],[-1,9,-1],[-1,-1,-1]]))
    adp=cv2.adaptiveThreshold(eq,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,15,8)
    wm=[None]
    def get_wm():
        if wm[0] is None: wm[0]=M._white_text_mask(img)
        return wm[0]
    if not lazy: wm[0]=M._white_text_mask(img)  # eager=现状
    order=[("gray",eq),("sharp",sharp),("white",get_wm if lazy else wm[0]),
           ("color",img),("th150",cv2.threshold(eq,150,255,cv2.THRESH_BINARY)[1]),
           ("th180",cv2.threshold(eq,180,255,cv2.THRESH_BINARY)[1]),("adaptive",adp)]
    best,bs,seq=[],-1,[]
    t0=time.perf_counter()
    for name,im in order:
        im=im() if callable(im) else im
        result,_=engine(im); result=result or []; seq.append(name)
        ac=sum(float(r[2]) for r in result)/len(result) if result else 0
        sc=len(result)+ac
        if sc>bs: bs,best=sc,result
        if name in("gray","sharp") and len(result)>=3 and ac>0.60: break
    dt=(time.perf_counter()-t0)*1000
    items=[{"text":str(r[1]),"confidence":float(r[2])} for r in best]
    return items,seq,dt


def med(x): return statistics.median(x)

def main():
    eng=M.make_ocr_engine()
    print("="*72)
    # (1) 难图分支
    hard=base_image(True)
    for _ in range(2): run(hard,eng)
    items,seq,dt=run(hard,eng,trace=True)
    print("[难图] 变体执行序列:",seq,"（共%d次推理）  耗时 %.0fms"%(len(seq),dt))
    for it in items: print("   选中 ->",it["text"],round(it["confidence"],2))
    # 各变体在难图上单独识别行数，看 white 是否更有用
    Pimg=cv2.resize(hard,None,fx=3,fy=3,interpolation=cv2.INTER_CUBIC)
    g=cv2.cvtColor(Pimg,cv2.COLOR_BGR2GRAY); e=cv2.createCLAHE(3.0,(8,8)).apply(g)
    wm=M._white_text_mask(Pimg)
    for name,im in (("gray",e),("white",wm),("adaptive",cv2.adaptiveThreshold(e,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,15,8))):
        r,_=eng(im); r=r or []
        ac=sum(float(x[2]) for x in r)/len(r) if r else 0
        print(f"   难图单变体 {name:8s} 行数={len(r)} 均conf={ac:.2f}")

    print("-"*72)
    # (2) 懒求值 A/B（清晰图，gray 命中常见路径）
    easy=base_image(False)
    for u in (False,True):
        for _ in range(3): run(easy,eng,lazy=u)
    ea,la=[],[]
    N=15
    for i in range(N):
        for lazy in (i%2==1, i%2==0):  # 交替
            _,_,dt=run(easy,eng,lazy=lazy)
            (la if lazy else ea).append(dt)
    items_e,_,_=run(easy,eng,lazy=False); items_l,_,_=run(easy,eng,lazy=True)
    oracle=[x["text"] for x in items_e]==[x["text"] for x in items_l]
    print("[常见路径 A/B] eager(现状) med=%.1fms | lazy med=%.1fms | delta=%+.1fms"%(
        med(ea),med(la),med(la)-med(ea)))
    print("[oracle] eager 与 lazy 最终文本一致?",oracle)
    print("="*72)

if __name__=="__main__":
    main()
