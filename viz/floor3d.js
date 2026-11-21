/* G.O.A.T. AI floor in 3D (adapted from the Software Farm floor). Draws the same scene as floor.js (same agents, cards and effects) with Three.js.
   Needs vendor/three.min.js (global THREE) and floor.js (window.Floor). */
(function(){
'use strict';
const D3 = {};
let F, renderer, scene, camera, canvas, active = false, ok = false;
const W = 480, H = 224, WALL = 11, LOW = 4;
const P = (x,y,h) => new THREE.Vector3(x-W/2, h||0, y-H/2);
const clamp = (v,a,b) => Math.max(a, Math.min(b, v));

// ---------- materials, geometry, boxes ----------
const mats = new Map(), geos = new Map();
function mat(color,opts){ const k=color+(opts?JSON.stringify(opts):''); if(!mats.has(k)) mats.set(k,new THREE.MeshLambertMaterial(Object.assign({color},opts||{}))); return mats.get(k); }
function geo(w,h,d){ const k=w+','+h+','+d; if(!geos.has(k)) geos.set(k,new THREE.BoxGeometry(w,h,d)); return geos.get(k); }
function box(w,h,d,color,x,y,z,parent,opts){ const m=new THREE.Mesh(geo(w,h,d),opts&&opts.isMaterial?opts:mat(color,opts)); m.position.set(x,y,z); m.castShadow=true; m.receiveShadow=true; (parent||scene).add(m); return m; }
// A box from 2D floor coordinates: x,y top-left, w wide, d deep, h tall, sitting at height base.
function b2(x,y,w,d,h,color,base,opts){ return box(w,h,d,color,x+w/2-W/2,(base||0)+h/2,y+d/2-H/2,null,opts); }

// ---------- canvas textures ----------
const texCache = new Map();
function canvasTex(key,w,h,draw){
  if(texCache.has(key)) return texCache.get(key);
  const c=document.createElement('canvas'); c.width=w; c.height=h; const g=c.getContext('2d'); draw(g,w,h);
  const t=new THREE.CanvasTexture(c); t.colorSpace=THREE.SRGBColorSpace; t.anisotropy=4;
  const v={tex:t,w,h,canvas:c,g}; texCache.set(key,v); return v;
}
function roundRect(g,x,y,w,h,r){ g.beginPath(); g.moveTo(x+r,y); g.arcTo(x+w,y,x+w,y+h,r); g.arcTo(x+w,y+h,x,y+h,r); g.arcTo(x,y+h,x,y,r); g.arcTo(x,y,x+w,y,r); g.closePath(); }
const PX = 6; // canvas pixels per world unit for sprites
function labelTex(text,color){
  return canvasTex('L|'+text+'|'+color, Math.ceil(text.length*15+18), 40, (g,w,h)=>{
    g.fillStyle='rgba(10,10,14,0.8)'; roundRect(g,0,4,w,h-8,8); g.fill();
    g.font='500 24px ui-monospace,Menlo,monospace'; g.fillStyle=color; g.textAlign='center'; g.textBaseline='middle'; g.fillText(text,w/2,h/2+1);
  });
}
function moodTex(icon){
  return canvasTex('M|'+icon, 88, 84, (g)=>{
    g.fillStyle='#f4f1e8'; roundRect(g,4,4,80,64,12); g.fill(); g.beginPath(); g.moveTo(20,66); g.lineTo(14,82); g.lineTo(34,66); g.fill();
    if(icon==='code'){ g.font='bold 30px ui-monospace,Menlo,monospace'; g.fillStyle='#378ADD'; g.textAlign='center'; g.textBaseline='middle'; g.fillText('</>',44,37); return; }
    const m=F.ICONS[icon]; for(let r=0;r<m.length;r++) for(let i=0;i<m[r].length;i++){ const c=F.ICON_PAL[m[r][i]]; if(c){ g.fillStyle=c; g.fillRect(24+i*8,16+r*8,8,8); } }
  });
}
function speechTex(text){
  const lines=F.wrapText(text,26), maxLen=Math.max(...lines.map(l=>l.length));
  return canvasTex('S|'+text, Math.ceil(maxLen*14.5+28), lines.length*28+34, (g,w,h)=>{
    g.fillStyle='#f4f1e8'; roundRect(g,2,2,w-4,h-18,12); g.fill(); g.beginPath(); g.moveTo(w/2-10,h-17); g.lineTo(w/2,h-2); g.lineTo(w/2+10,h-17); g.fill();
    g.font='24px ui-monospace,Menlo,monospace'; g.fillStyle='#26215C'; g.textBaseline='top';
    lines.forEach((l,i)=>g.fillText(l,14,12+i*28));
  });
}
function stampTex(text,color){
  return canvasTex('T|'+text+color, Math.ceil(text.length*30+40), 72, (g,w,h)=>{
    g.strokeStyle=color; g.lineWidth=7; g.strokeRect(6,6,w-12,h-12);
    g.font='bold 44px ui-monospace,Menlo,monospace'; g.fillStyle=color; g.textAlign='center'; g.textBaseline='middle'; g.fillText(text,w/2,h/2+2);
  });
}
function sprite(t,parent,opts){
  const m=new THREE.SpriteMaterial(Object.assign({map:t.tex,transparent:true,depthTest:false},opts||{}));
  const s=new THREE.Sprite(m); s.renderOrder=10; s.scale.set(t.w/PX,t.h/PX,1); (parent||scene).add(s); return s;
}
function setSpriteTex(s,t){ if(s.material.map!==t.tex){ s.material.map=t.tex; s.material.needsUpdate=true; } s.scale.set(t.w/PX,t.h/PX,1); }

// ---------- static scene ----------
let truckG, bellM, clockTex, scoreTex, glowMats=[], boardH, lampPool=[], fixedLamps=[];
function checkerTex(r){
  return canvasTex('F|'+r.id, r.w*4, r.h*4, (g,w,h)=>{
    g.fillStyle=r.floor; g.fillRect(0,0,w,h);
    g.fillStyle='rgba(255,255,255,0.05)'; for(let y=0;y<r.h;y+=8) for(let x=0;x<r.w;x+=8) if(((x+y)/8)%2) g.fillRect(x*4,y*4,32,32);
  });
}
function roomLabel(r){
  const t=canvasTex('R|'+r.name, r.name.length*22+24, 40, (g,w,h)=>{ g.fillStyle='rgba(0,0,0,0.45)'; roundRect(g,0,0,w,h,6); g.fill(); g.font='500 26px ui-monospace,Menlo,monospace'; g.fillStyle='#e8e6df'; g.textBaseline='middle'; g.fillText(r.name,12,h/2+1); });
  const w=Math.min(r.w-8,t.w/4.2), h=w*t.h/t.w;
  const m=new THREE.Mesh(new THREE.PlaneGeometry(w,h), new THREE.MeshBasicMaterial({map:t.tex,transparent:true}));
  m.rotation.x=-Math.PI/2; const p=P(r.x+4+w/2, r.top?r.y+r.h-5-h/2:r.y+8+h/2, 0.62); m.position.copy(p); scene.add(m);
}
function desk(x,y,w){ b2(x,y,w,8,6,'#7a5230'); b2(x,y,w,8,0.6,'#9a6a40',6); }
function monitor(x,y){ b2(x,y+0.4,9,1.2,6.4,'#1e1e24',6.6); b2(x+1,y+1.65,7,0.15,4.6,'#2f5a74',7.5,{emissive:'#1d3e52'}); b2(x+3.5,y+0.6,2,1,1,'#1e1e24',6.6); }
function chair(sx,sy){ b2(sx-3,sy-3,6,6,3,'#3a2a20'); b2(sx-3,sy+2.5,6,1.2,7,'#4a3628',3); }
function plant(x,y){ b2(x,y+4,5,4,3,'#993c1d'); b2(x-1,y,7,7,4,'#3b6d11',3); b2(x+1,y+1,4,4,2,'#639922',7); }
function wallSeg(x,y,w,d,h){ b2(x,y,w,d,h,'#2b2b35'); b2(x,y,w,d,0.6,'#3a3a46',h); }
function buildStatic(){
  const R=F.R;
  box(W+900,1,H+900,'#1d1d24',0,-0.5,0).castShadow=false;
  b2(4,98,472,30,0.4,'#5b574d');
  for(let x=4;x<476;x+=8) b2(x,112.5,4,1,0.05,'#6b675c',0.4);
  for(const r of F.ROOMS){
    const fm=new THREE.MeshLambertMaterial({map:checkerTex(r).tex});
    const f=box(r.w,0.6,r.h,null,r.x+r.w/2-W/2,0.3,r.y+r.h/2-H/2,null,fm); f.castShadow=false;
    const dl=r.door-6, dr=r.door+6;
    if(r.top){ wallSeg(r.x,r.y,r.w,2,WALL); wallSeg(r.x,r.y+r.h-2,dl-r.x,2,LOW); wallSeg(dr,r.y+r.h-2,r.x+r.w-dr,2,LOW); }
    else { wallSeg(r.x,r.y,dl-r.x,2,WALL); wallSeg(dr,r.y,r.x+r.w-dr,2,WALL); wallSeg(r.x,r.y+r.h-2,r.w,2,LOW); }
    wallSeg(r.x,r.y,2,r.h,WALL*0.8); wallSeg(r.x+r.w-2,r.y,2,r.h,WALL*0.8);
    roomLabel(r);
  }
  for(const s of F.STAGES){ const {x,y}=R[s];
    b2(x+4,y,56,2.2,2,F.COL[s],WALL); desk(x+6,y+30,22); desk(x+36,y+30,22); monitor(x+12,y+30); monitor(x+42,y+30);
    b2(x+18,y+73,28,7,1,'#5a4a3a'); plant(x+54,y+16); }
  for(const k of Object.keys(F.seats)) for(const s of F.seats[k].list) chair(s[0],s[1]);
  chair(...F.HUMAN_HOME); chair(...F.FOREMAN_HOME);
  // board: a standing pin board along the north wall
  const b=R.board; boardH={x0:b.x+4,z:b.y+9};
  b2(b.x+4,b.y+4,58,3,30,'#5a4020',1); b2(b.x+5,b.y+6.9,56,0.4,28,'#8a6a3a',2);
  // dock
  const d=R.dock;
  b2(d.x+6,d.y+52,16,10,9,'#9a7a4a'); b2(d.x+6,d.y+63,16,10,9,'#9a7a4a'); b2(d.x+8,d.y+54,12,6,7,'#b08a54',9);
  for(let i=0;i<6;i++) b2(d.x+30+i*8,d.y+86,4,2,0.1,'#EFC72E',0.6);
  truckG=new THREE.Group(); truckG.position.copy(P(d.x+50,d.y+58,0)); scene.add(truckG);
  box(24,13,12,'#e8e6df',12,8,6,truckG); box(8,9,12,'#D85A30',28,6,6,truckG); box(0.4,4,8,'#9fd3ef',32.2,8,6,truckG);
  const farmTex=canvasTex('GOAT',128,48,(g,w,h)=>{ g.fillStyle='#e8e6df'; g.fillRect(0,0,w,h); g.font='bold 34px ui-monospace,Menlo,monospace'; g.fillStyle='#3B9E52'; g.textAlign='center'; g.textBaseline='middle'; g.fillText('GOAT',w/2,h/2+2); });
  const fp=new THREE.Mesh(new THREE.PlaneGeometry(16,6),new THREE.MeshBasicMaterial({map:farmTex.tex})); fp.position.set(12,9,12.05); truckG.add(fp);
  for(const wx of [5,19,28]){ const wh=new THREE.Mesh(new THREE.CylinderGeometry(2.4,2.4,12.6,12),mat('#1b1b1b')); wh.rotation.x=Math.PI/2; wh.position.set(wx,1.8,6); wh.castShadow=true; truckG.add(wh); }
  // coordinator
  const co=R.coord; desk(co.x+8,co.y+40,30); monitor(co.x+12,co.y+40); monitor(co.x+24,co.y+40); desk(co.x+46,co.y+40,20);
  clockTex=canvasTex('clock',96,96,()=>{}); const cm=new THREE.Mesh(new THREE.CircleGeometry(6,24),new THREE.MeshBasicMaterial({map:clockTex.tex}));
  cm.position.copy(P(co.x+60,co.y+2.2,6.5)); scene.add(cm);
  bellM=new THREE.Mesh(new THREE.ConeGeometry(2.2,3.2,12),mat('#EFC72E')); bellM.position.copy(P(co.x+50,co.y+1,13.2)); scene.add(bellM);
  scoreTex=canvasTex('score',384,150,()=>{});
  b2(co.x+3,co.y+84,66,2,30,'#6a4a2a',0);
  const sm=new THREE.Mesh(new THREE.PlaneGeometry(62,24),new THREE.MeshBasicMaterial({map:scoreTex.tex})); sm.position.copy(P(co.x+36,co.y+86.1,17)); scene.add(sm);
  // gate
  const ga=R.gate; desk(ga.x+16,ga.y+36,48); plant(ga.x+68,ga.y+20);
  for(const [w,d,x,y] of [[50,0.6,ga.x+15,ga.y+35],[50,0.6,ga.x+15,ga.y+44.4],[0.6,10,ga.x+15,ga.y+35],[0.6,10,ga.x+64.4,ga.y+35]]){
    const m=new THREE.MeshBasicMaterial({color:'#EF9F27',transparent:true,opacity:0}); glowMats.push(m); b2(x,y,w,d,0.4,null,6.7,m); }
  // lounge
  const lo=R.lounge; b2(lo.x+6,lo.y+46,64,4,10,'#5a2f4c'); b2(lo.x+6,lo.y+50,64,10,4,'#6a3a5a'); b2(lo.x+4,lo.y+46,3,14,6,'#5a2f4c'); b2(lo.x+69,lo.y+46,3,14,6,'#5a2f4c');
  b2(lo.x+22,lo.y+72,30,8,4,'#7a5230'); b2(lo.x+30,lo.y+74,3,3,2.5,'#f4f1e8',4); plant(lo.x+64,lo.y+74);
  // parking
  const pa=R.parking; for(let i=0;i<4;i++) b2(pa.x+8+i*22,pa.y+24,1,44,0.1,'#cfcbbf',0.6);
  b2(pa.x+30,pa.y+80,20,3,1.5,'#EFC72E');
  // project manager
  const sp=R.split; desk(sp.x+12,sp.y+40,52); b2(sp.x+18,sp.y+75,38,7,1,'#5a4a3a');
  const saw=new THREE.Mesh(new THREE.CylinderGeometry(3,3,0.6,16),mat('#aaaaaa')); saw.position.copy(P(sp.x+20,sp.y+44,7)); scene.add(saw);
  // library: shelf unit on the north wall
  const li=R.library; b2(li.x+6,li.y+3,76,2,17,'#5a3a1e'); for(const h of [1.2,9.5,16.4]) b2(li.x+6,li.y+3,76,8,0.8,'#6a4a2a',h);
  b2(li.x+6,li.y+3,1,8,17,'#5a3a1e'); b2(li.x+81,li.y+3,1,8,17,'#5a3a1e');
  desk(li.x+30,li.y+56,32); b2(li.x+72,li.y+60,8,14,14,'#6a4a2a');
  const books=['#7F77DD','#D85A30','#1D9E75','#EF9F27','#D4537E','#378ADD']; books.forEach((c,i)=>b2(li.x+73,li.y+61+i*2,6,1.6,4,c,14));
}

// ---------- characters ----------
class Char{
  constructor(a){
    this.a=a; const p=a.pal; this.g=new THREE.Group(); scene.add(this.g); this.yaw=0; this.px=a.x; this.py=a.y;
    const g=this.g, k=F.roleKey(a.role)==='other'&&(a.role==='coord'||a.role==='human')?a.role:(a.role==='coord'||a.role==='human'?a.role:F.roleKey(a.role));
    this.k=k;
    const limb=(x,y,parent)=>{ const j=new THREE.Group(); j.position.set(x,y,0); parent.add(j); return j; };
    this.body=new THREE.Group(); g.add(this.body);
    this.hipL=limb(-1.5,6,this.body); this.hipR=limb(1.5,6,this.body);
    for(const hip of [this.hipL,this.hipR]){ box(2.6,5,2.6,p.p,0,-2.5,0,hip); box(2.8,1.2,3.4,p.b,0,-5.4,0.3,hip); }
    box(6.2,6,3.8,p.c,0,9,0,this.body); box(6.4,1,4,p.d,0,6.5,0,this.body);
    this.armL=limb(-4,11.6,this.body); this.armR=limb(4,11.6,this.body);
    for(const arm of [this.armL,this.armR]){ box(1.9,4.6,1.9,p.c,0,-2.3,0,arm); box(1.9,1.4,1.9,p.s,0,-5.2,0,arm); }
    this.head=limb(0,12,this.body);
    box(5,5,5,p.s,0,2.5,0,this.head); box(5.4,1.6,5.4,p.h,0,5,0,this.head); box(5.4,3.4,1,p.h,0,3.4,-2.4,this.head);
    box(0.4,2.6,4.6,p.h,-2.6,3.6,-0.2,this.head); box(0.4,2.6,4.6,p.h,2.6,3.6,-0.2,this.head);
    this.eyes=[box(0.9,0.9,0.3,'#1b1b1b',-1.2,2.7,2.55,this.head), box(0.9,0.9,0.3,'#1b1b1b',1.2,2.7,2.55,this.head)];
    const h=this.head, aR=this.armR, aL=this.armL, body=this.body;
    switch(k){
      case 'intake': box(5.8,1.6,5.8,p.d,0,6,0,h); box(4.6,0.5,3,p.d,0,5.4,3.6,h); break;
      case 'plan': box(5.4,0.5,0.3,'#1b1b1b',0,3.6,2.7,h); box(1.6,1.4,0.3,'#9fd3ef',-1.2,2.7,2.75,h); box(1.6,1.4,0.3,'#9fd3ef',1.2,2.7,2.75,h); break;
      case 'qa': { const ring=new THREE.Mesh(new THREE.TorusGeometry(1.6,0.4,6,14),mat('#1b1b1b')); ring.position.set(0,-7.8,1.4); aR.add(ring);
        box(0.6,2,0.6,'#7a5230',0,-6,1.2,aR); const lens=new THREE.Mesh(new THREE.CircleGeometry(1.3,12),mat('#9fd3ef',{transparent:true,opacity:0.7})); lens.position.set(0,-7.8,1.45); aR.add(lens); break; }
      case 'review': box(3.4,4.4,0.5,'#c9a26a',0,-5.6,1.6,aL); box(2.8,3,0.2,'#f4f1e8',0,-5.8,1.9,aL); box(0.5,0.5,3,'#D33A3A',2.6,4,0,h); break;
      case 'implement': box(6,6,1,p.d,0,3,-3,h); box(1,5.6,5.6,p.d,-3,3,0,h); box(1,5.6,5.6,p.d,3,3,0,h); box(6,1,6,p.d,0,5.8,0,h);
        box(1.8,2,1.8,'#f4f1e8',0,-6.4,1.4,aR); box(0.4,1,0.8,'#f4f1e8',1.1,-6.3,1.4,aR); break;
      case 'pm': box(6.8,1.6,4.6,'#EFC72E',0,11.6,0,body); box(1.4,3.4,0.6,'#EFC72E',1.2,9.6,2.2,body); break;
      case 'retro': box(2.6,2.2,2.6,p.h,0,6.8,-0.6,h); box(5.4,0.5,0.3,'#1b1b1b',0,3.6,2.7,h); box(1.6,1.4,0.3,'#c9c2e8',-1.2,2.7,2.75,h); box(1.6,1.4,0.3,'#c9c2e8',1.2,2.7,2.75,h); break;
      case 'coord': box(6.2,1.8,6.2,'#EFC72E',0,5.8,0,h); box(7.4,0.4,7.4,'#EFC72E',0,4.9,0,h); box(6.5,1,4.1,'#EFC72E',0,9,0,body); box(1,6,4.1,'#E8862A',-2.6,9,0,body); box(1,6,4.1,'#E8862A',2.6,9,0,body); break;
      case 'human': box(1,4.6,0.3,'#D33A3A',0,9.4,1.95,body); box(1.6,0.8,0.3,'#D33A3A',0,11.6,1.95,body); break;
    }
    this.label=sprite(labelTex(a.label||' ','#e8e6df')); this.bubble=sprite(moodTex('gear')); this.bubble.visible=false;
    this.ring=new THREE.Mesh(new THREE.RingGeometry(5,6.2,24),new THREE.MeshBasicMaterial({color:'#85B7EB',side:THREE.DoubleSide})); this.ring.rotation.x=-Math.PI/2; this.ring.visible=false; scene.add(this.ring);
    this.zzz=sprite(canvasTex('zzz',48,48,(g)=>{ g.font='bold 34px ui-monospace,Menlo,monospace'; g.fillStyle='#c9c2e8'; g.fillText('z',10,36); })); this.zzz.visible=false;
    g.traverse(o=>{ o.userData.agent=a; });
  }
  update(dt){
    const a=this.a, g=this.g, T=F.T;
    const dx=a.x-this.px, dy=a.y-this.py; this.px=a.x; this.py=a.y;
    if(Math.hypot(dx,dy)>0.01) this.target=Math.atan2(dx,dy);
    let yaw=this.target!==undefined?this.target:this.yaw;
    const pos=P(a.x,a.y,0);
    g.rotation.set(0,0,0); this.body.position.set(0,0,0); this.head.rotation.set(0,0,0);
    [this.hipL,this.hipR,this.armL,this.armR].forEach(j=>j.rotation.set(0,0,0));
    if(a.state==='sleep'){
      g.position.set(pos.x-6,7,pos.z); g.rotation.set(0,0,-Math.PI/2); this.yaw=0;
      this.armL.rotation.x=-0.3; this.armR.rotation.x=0.2;
    } else if(a.state==='work'){
      yaw=Math.PI; g.position.set(pos.x,0,pos.z);
      this.body.position.y=-2.5;
      this.hipL.rotation.x=this.hipR.rotation.x=-Math.PI/2;
      const t=Math.sin(a.anim*Math.PI)*0.18;
      this.armL.rotation.x=-1.15+t; this.armR.rotation.x=-1.15-t;
      if(a.role==='human') { this.armL.rotation.x=-0.9; this.armR.rotation.x=-0.9; this.head.rotation.x=0.25; }
    } else {
      g.position.set(pos.x,0,pos.z);
      if(a.state==='walk'){ const s=Math.sin(a.anim*Math.PI)*(a.slump?0.35:0.65); this.hipL.rotation.x=s; this.hipR.rotation.x=-s; this.armL.rotation.x=-s*0.8; this.armR.rotation.x=s*0.8; g.position.y=Math.abs(Math.cos(a.anim*Math.PI))*0.5; }
      if(a.card){ this.armL.rotation.x=this.armR.rotation.x=-0.9; }
      if(a.slump){ this.head.rotation.x=0.5; this.body.position.y=-0.5; }
    }
    // smooth turn
    let d=yaw-this.yaw; while(d>Math.PI) d-=Math.PI*2; while(d<-Math.PI) d+=Math.PI*2; this.yaw+=d*Math.min(1,dt*12);
    if(a.state!=='sleep') g.rotation.y=this.yaw;
    // label, bubble, ring, zzz
    const col=F.COL[F.roleKey(a.role)]||F.COL.other;
    const lh=a.state==='sleep'?14:a.state==='work'?21:24;
    if(a.label){ this.label.visible=true; setSpriteTex(this.label,labelTex(a.label,col)); this.label.position.set(pos.x,lh,pos.z); } else this.label.visible=false;
    let bub=null, side=0;
    if(a.say&&a.say.until>T) bub=speechTex(a.say.text);
    else if(a.state==='sleep'&&a.question) bub=speechTex(a.question);
    else if(a.state==='work'&&((T+a.id*1.7)%6)<2){ bub=moodTex(F.moodIcon(a)); side=1; }
    if(bub){ this.bubble.visible=true; setSpriteTex(this.bubble,bub); const s=this.bubble.scale; this.bubble.position.set(pos.x+(side?7:0),lh+4+s.y/2+(side?0:1),pos.z); }
    else this.bubble.visible=false;
    if(a.state==='sleep'){ this.zzz.visible=true; const z=(T*1.2)%1; this.zzz.position.set(pos.x+10,10+z*6,pos.z); this.zzz.material.opacity=1-z; } else this.zzz.visible=false;
    const sel=F.sel&&F.sel.o===a; this.ring.visible=sel; if(sel) this.ring.position.set(pos.x,0.8,pos.z);
    // hand position for carried cards
    const fwd=new THREE.Vector3(Math.sin(this.yaw),0,Math.cos(this.yaw));
    this.hand=pos.clone().add(fwd.multiplyScalar(3.6)); this.hand.y=7.5+g.position.y;
  }
  dispose(){ scene.remove(this.g,this.label,this.bubble,this.ring,this.zzz); }
}

// ---------- cards ----------
const SIZE = F => s => F.SIZECOL[s]||F.NOSIZE;
class CardV{
  constructor(c){
    this.c=c; this.g=new THREE.Group(); scene.add(this.g);
    this.m=new THREE.MeshLambertMaterial({color:'#ffffff'});
    this.flat=box(7,0.7,5,null,0,0,0,this.g,this.m);
    this.book=box(4,8,5,null,0,4,0,this.g,this.m); this.band=box(4.1,0.8,5.1,'#555',0,5.5,0,this.g);
    this.pr=box(1.6,0.4,1.6,'#1D9E75',2.2,0.5,1.2,this.g);
    this.el=[box(1.3,1.3,1.3,'#c9c2e8',-2,3,0,this.g),box(1.3,1.3,1.3,'#c9c2e8',0.6,3,0,this.g)];
    this.lbl=null;
    this.g.traverse(o=>{ o.userData.card=c; });
  }
  update(){
    const c=this.c, T=F.T, g=this.g;
    const col=(F.SIZECOL[c.size]||F.NOSIZE); this.m.color.set(col); this.band.material=mat(F.shade(col,0.6));
    const sel=F.sel&&F.sel.o===c;
    this.m.emissive.set(sel?'#2a4a7a':c.attention?'#EFC72E':'#000000'); this.m.emissiveIntensity=sel?1:c.attention?0.25+0.25*Math.sin(T*5):0;
    let onShelf=false, onWall=false;
    g.rotation.set(0,0,0); g.scale.set(1,1,1);
    if(c.holder){
      const h=c.holder, cv=chars.get(h);
      if(h.state==='work'&&h.seat){ g.position.copy(P(h.seat[2]+3.5,h.seat[3]+2.5,7)); }
      else if(cv&&cv.hand){ g.position.copy(cv.hand); g.rotation.y=cv.yaw; g.rotation.x=-0.5; }
      else g.position.copy(P(h.x,h.y,8));
    } else if(c.slots===F.wall){
      const row=Math.round((c.y-(F.R.board.y+16))/10); onWall=true;
      g.position.set(c.x+3.5-W/2, 27-row*5.2, F.R.board.y+7.7-H/2); g.rotation.x=Math.PI/2;
    } else if(c.slots===F.shelf){
      const row=Math.round((c.y-(F.R.library.y+16))/16); onShelf=true;
      g.position.copy(P(c.x+2,F.R.library.y+5.5,row?2:10.3)); g.scale.set(1,0.8,1);
    } else {
      const h=c.slots===F.gateIn?7:c.slots===F.dock||c.slots===F.parking?1:1.8;
      g.position.copy(P(c.x+3.5,c.y+2.5,h));
    }
    this.flat.visible=!onShelf; this.book.visible=onShelf; this.band.visible=onShelf;
    this.pr.visible=!!c.pr&&!onShelf; this.el.forEach(e=>e.visible=!!c.elsewhere&&!onShelf&&!c.holder);
    if(onWall){
      if(!this.lbl) this.lbl=sprite(labelTex(String(c.label).slice(0,8),'#f4ecd8'));
      setSpriteTex(this.lbl,labelTex(String(c.label).slice(0,8),'#f4ecd8')); this.lbl.visible=true; this.lbl.scale.multiplyScalar(0.7);
      this.lbl.position.set(g.position.x+9.5,g.position.y,g.position.z+1);
    } else if(this.lbl) this.lbl.visible=false;
  }
  dispose(){ scene.remove(this.g); if(this.lbl) scene.remove(this.lbl); }
}

// ---------- effects ----------
const fxViews = new Map();
let bugMeshes = [];
const bugTex = () => canvasTex('bug',40,32,(g,w,h)=>{ g.fillStyle='#E24B4A'; g.fillRect(0,0,w,h); const m=F.ICONS.bug; for(let r=0;r<5;r++) for(let i=0;i<5;i++){ const ch=m[r][i]; if(ch==='k'){ g.fillStyle='#1b1b1b'; g.fillRect(10+i*4,6+r*4,4,4);} else if(ch==='r'){ g.fillStyle='#f4f1e8'; g.fillRect(10+i*4,6+r*4,4,4);} } });
function updateFx(){
  const T=F.T, live=new Set(F.fx);
  for(const [e,v] of fxViews) if(!live.has(e)){ scene.remove(v.obj); fxViews.delete(e); }
  for(const e of F.fx){
    let v=fxViews.get(e); const age=T-e.t0;
    if(e.kind==='stamp'){
      if(!v){ v={obj:sprite(stampTex(e.text,e.color))}; fxViews.set(e,v); }
      const t=stampTex(e.text,e.color), sc=(age<0.15?2.2-1.2*(age/0.15):1)*0.9;
      v.obj.scale.set(t.w/PX*sc,t.h/PX*sc,1); v.obj.position.copy(P(e.x,e.y,30)); v.obj.material.opacity=age>e.dur-0.5?Math.max(0,(e.dur-age)/0.5):1;
      v.obj.material.rotation=-0.16;
    } else if(e.kind==='confetti'){
      if(!v){ const n=e.parts.length, im=new THREE.InstancedMesh(new THREE.BoxGeometry(1.4,0.3,1),new THREE.MeshBasicMaterial(),n);
        e.parts.forEach((p,i)=>{ im.setColorAt(i,new THREE.Color(p.c)); p.vz=p.vz===undefined?(Math.random()-0.5)*60:p.vz; p.sp=Math.random()*10; });
        if(im.instanceColor) im.instanceColor.needsUpdate=true; scene.add(im); v={obj:im}; fxViews.set(e,v); }
      const m=new THREE.Matrix4(), q=new THREE.Quaternion(), s=new THREE.Vector3(1,1,1), base=P(e.x,e.y,12);
      e.parts.forEach((p,i)=>{ const y=base.y+(-p.vy)*age*0.8-30*age*age; q.setFromEuler(new THREE.Euler(p.sp*age,p.sp*age*0.7,0));
        m.compose(new THREE.Vector3(base.x+p.vx*age*0.8, Math.max(0.6,y), base.z+p.vz*age*0.6),q,s); v.obj.setMatrixAt(i,m); });
      v.obj.instanceMatrix.needsUpdate=true;
    } else if(e.kind==='smoke'){
      if(!v){ const grp=new THREE.Group(); for(let k=0;k<7;k++){ const sm=new THREE.Mesh(new THREE.SphereGeometry(1.6,8,6),new THREE.MeshBasicMaterial({color:'#8a8a94',transparent:true,opacity:0.6})); grp.add(sm); } scene.add(grp); v={obj:grp}; fxViews.set(e,v); }
      const base=P(e.x,e.y,12);
      v.obj.children.forEach((sm,k)=>{ const a=((age+k*0.25)%1.6); sm.position.set(base.x+Math.sin(k*1.7+a*4)*2,base.y+a*14,base.z); sm.scale.setScalar(0.6+a); sm.material.opacity=Math.max(0,0.7-a/1.6)*(age>e.dur-1?Math.max(0,e.dur-age):1); });
    }
  }
  if(bugMeshes.length!==F.bugs.length){
    bugMeshes.forEach(m=>scene.remove(m)); bugMeshes=[];
    const bm=new THREE.MeshLambertMaterial({map:bugTex().tex});
    F.bugs.forEach((b,i)=>{ const m=box(9,5,0.8,null,F.R.board.x+11.5+i*11-W/2,35,F.R.board.y+5.5-H/2,null,bm); bugMeshes.push(m); });
  }
}

// ---------- weather and light ----------
let hemi, sun, clouds=[], rain, rainPos, skyCol=new THREE.Color();
function buildLights(){
  hemi=new THREE.HemisphereLight('#dfe9ff','#4a4038',1.2); scene.add(hemi);
  sun=new THREE.DirectionalLight('#fff4e0',2.2); sun.position.set(-160,260,180); sun.castShadow=true;
  sun.shadow.mapSize.set(2048,2048); const sc=sun.shadow.camera; sc.left=-270; sc.right=270; sc.top=160; sc.bottom=-160; sc.near=10; sc.far=700; sun.shadow.bias=-0.0006;
  scene.add(sun); scene.add(sun.target);
  for(let i=0;i<6;i++){ const l=new THREE.PointLight('#ffbe6e',0,50,2); scene.add(l); lampPool.push(l); }
  for(const [x,y] of [[F.R.coord.x+22,F.R.coord.y+40],[F.R.gate.x+40,F.R.gate.y+38]]){ const l=new THREE.PointLight('#ffbe6e',0,50,2); l.position.copy(P(x,y,16)); scene.add(l); fixedLamps.push(l); }
  for(let i=0;i<7;i++){ const cg=new THREE.Group(); const cm=new THREE.MeshLambertMaterial({color:'#f4f4f8',transparent:true,opacity:0.92});
    for(let k=0;k<4;k++) box(14+Math.random()*10,5+Math.random()*3,10+Math.random()*8,null,k*9-12,Math.random()*3,Math.random()*6-3,cg,cm).castShadow=true;
    cg.userData={off:i*80+Math.random()*40,z:-110+Math.random()*220,y:70+Math.random()*12,m:cm}; scene.add(cg); clouds.push(cg); }
  const n=600; rainPos=new Float32Array(n*3); for(let i=0;i<n;i++){ rainPos[i*3]=(Math.random()-0.5)*W; rainPos[i*3+1]=Math.random()*90; rainPos[i*3+2]=(Math.random()-0.5)*H; }
  const rg=new THREE.BufferGeometry(); rg.setAttribute('position',new THREE.BufferAttribute(rainPos,3));
  rain=new THREE.Points(rg,new THREE.PointsMaterial({color:'#9fc3e8',size:1.4,transparent:true,opacity:0.8})); scene.add(rain);
}
function updateLight(dt){
  const L=F.daylight(), n=L.night, waiting=F.gateIn.items().length, T=F.T;
  const day=new THREE.Color('#8cc4ee'), night=new THREE.Color('#0e1430'), dusk=new THREE.Color('#f0a070'), grey=new THREE.Color('#7f8794');
  skyCol.copy(day).lerp(night,n).lerp(dusk,L.dusk*0.5); if(waiting>=1) skyCol.lerp(grey,waiting>=3?0.45:0.2);
  scene.background=skyCol; scene.fog.color.copy(skyCol);
  hemi.intensity=1.25*(1-n)+0.45*n; sun.intensity=(waiting>=3?1.2:2.3)*(1-n)+0.55*n;
  sun.color.set(n>0.6?'#9fb4ff':L.dusk>0.3?'#ffc890':'#fff4e0');
  const working=F.agents.filter(a=>a.state==='work'&&a.seat);
  lampPool.forEach((l,i)=>{ const a=working[i]; if(a&&n>0.05){ l.intensity=320*n; l.position.copy(P(a.seat[2]+3,a.seat[3],14)); } else l.intensity=0; });
  fixedLamps.forEach(l=>l.intensity=200*n);
  const nc=waiting>=3?7:waiting>=1?3:0;
  clouds.forEach((c,i)=>{ c.visible=i<nc; const u=c.userData; c.position.set(((T*5+u.off)%(W+120))-W/2-60,u.y,u.z); u.m.color.set(waiting>=3?'#8d909c':'#f4f4f8'); });
  rain.visible=waiting>=3;
  if(rain.visible){ for(let i=0;i<rainPos.length;i+=3){ rainPos[i+1]-=dt*F.speed*110; if(rainPos[i+1]<0) rainPos[i+1]=85; } rain.geometry.attributes.position.needsUpdate=true; }
  glowMats.forEach(m=>m.opacity=waiting?0.4+0.4*Math.sin(T*4):0);
}
let lastClock=-1, lastScore=-1;
function updateProps(){
  const T=F.T, now=Date.now();
  if(Math.floor(now/1000)!==lastClock){ lastClock=Math.floor(now/1000); const g=clockTex.g, d=new Date();
    g.clearRect(0,0,96,96); g.fillStyle='#e8e6df'; g.beginPath(); g.arc(48,48,44,0,Math.PI*2); g.fill(); g.lineWidth=6; g.strokeStyle='#2a2a30'; g.stroke();
    const hand=(ang,len,w)=>{ g.beginPath(); g.moveTo(48,48); g.lineTo(48+Math.sin(ang)*len,48-Math.cos(ang)*len); g.lineWidth=w; g.strokeStyle='#1b1b1b'; g.lineCap='round'; g.stroke(); };
    hand(((d.getHours()%12)+d.getMinutes()/60)/12*Math.PI*2,24,6); hand((d.getMinutes()+d.getSeconds()/60)/60*Math.PI*2,36,4);
    clockTex.tex.needsUpdate=true; }
  if(Math.floor(now/500)!==lastScore){ lastScore=Math.floor(now/500); const g=scoreTex.g;
    g.fillStyle='#1d2a22'; g.fillRect(0,0,384,150); g.font='26px ui-monospace,Menlo,monospace'; g.fillStyle='#d8f0c8'; g.textBaseline='top';
    (F.hooks.scoreLines()||[]).slice(0,4).forEach((l,i)=>g.fillText(l,14,10+i*34)); scoreTex.tex.needsUpdate=true; }
  const age=T-F.bellT; bellM.rotation.z=age<1.3?Math.sin(age*30)*0.4*(1-age/1.3):0;
  const ta=T-F.truck.honk; truckG.position.y=ta<0.9?(Math.floor(ta*12)%2)*0.8:0;
}

// ---------- camera ----------
const orbit={theta:0, phi:0.78, r:440, target:new THREE.Vector3(0,0,2)};
const HOME={theta:0, phi:0.78, r:440, target:new THREE.Vector3(0,0,2)};
function applyCam(){ const t=orbit.target; camera.position.set(t.x+orbit.r*Math.sin(orbit.phi)*Math.sin(orbit.theta), t.y+orbit.r*Math.cos(orbit.phi), t.z+orbit.r*Math.sin(orbit.phi)*Math.cos(orbit.theta)); camera.lookAt(t); }
function setupControls(){
  const ptrs=new Map(); let moved=0, pinch=0;
  canvas.addEventListener('contextmenu',e=>e.preventDefault());
  canvas.addEventListener('pointerdown',e=>{ canvas.setPointerCapture(e.pointerId); ptrs.set(e.pointerId,{x:e.clientX,y:e.clientY,b:e.button,shift:e.shiftKey}); moved=0; if(ptrs.size===2){ const [a,b]=[...ptrs.values()]; pinch=Math.hypot(a.x-b.x,a.y-b.y); } });
  canvas.addEventListener('pointermove',e=>{
    const p=ptrs.get(e.pointerId); if(!p) return; const dx=e.clientX-p.x, dy=e.clientY-p.y; moved+=Math.abs(dx)+Math.abs(dy);
    if(ptrs.size===2){ p.x=e.clientX; p.y=e.clientY; const [a,b]=[...ptrs.values()], d=Math.hypot(a.x-b.x,a.y-b.y); if(pinch){ orbit.r=clamp(orbit.r*pinch/d,120,900); } pinch=d; applyCam(); return; }
    p.x=e.clientX; p.y=e.clientY;
    if(p.b===2||p.shift){ const right=new THREE.Vector3().setFromMatrixColumn(camera.matrix,0); const fwd=new THREE.Vector3(Math.sin(orbit.theta),0,Math.cos(orbit.theta));
      const k=orbit.r/600; orbit.target.addScaledVector(right,-dx*k).addScaledVector(fwd,-dy*k); orbit.target.x=clamp(orbit.target.x,-260,260); orbit.target.z=clamp(orbit.target.z,-140,140); }
    else { orbit.theta-=dx*0.006; orbit.phi=clamp(orbit.phi-dy*0.005,0.2,1.42); }
    applyCam();
  });
  const up=e=>{ const p=ptrs.get(e.pointerId); ptrs.delete(e.pointerId); if(p&&moved<6&&ptrs.size===0) pick(e); if(ptrs.size<2) pinch=0; };
  canvas.addEventListener('pointerup',up); canvas.addEventListener('pointercancel',e=>ptrs.delete(e.pointerId));
  canvas.addEventListener('wheel',e=>{ e.preventDefault(); orbit.r=clamp(orbit.r*(1+e.deltaY*0.001),120,900); applyCam(); },{passive:false});
  canvas.addEventListener('dblclick',()=>{ Object.assign(orbit,{theta:HOME.theta,phi:HOME.phi,r:HOME.r}); orbit.target.copy(HOME.target); applyCam(); });
}
const ray=new THREE.Raycaster();
function pick(e){
  const r=canvas.getBoundingClientRect(); const m=new THREE.Vector2(((e.clientX-r.left)/r.width)*2-1, -((e.clientY-r.top)/r.height)*2+1);
  ray.setFromCamera(m,camera);
  const targets=[]; chars.forEach(v=>targets.push(v.g)); cardsV.forEach(v=>targets.push(v.g));
  const hit=ray.intersectObjects(targets,true).find(h=>h.object.userData.agent||h.object.userData.card);
  F.sel = hit ? (hit.object.userData.agent?{type:'agent',o:hit.object.userData.agent}:{type:'card',o:hit.object.userData.card}) : null;
  if(F.hooks.onSelect) F.hooks.onSelect(F.sel);
}

// ---------- sync and render ----------
const chars=new Map(), cardsV=new Map();
function sync(dt){
  const live=new Set(F.agents);
  for(const [a,v] of chars) if(!live.has(a)){ v.dispose(); chars.delete(a); }
  for(const a of F.agents){ let v=chars.get(a); if(!v){ v=new Char(a); chars.set(a,v); } v.update(dt); }
  const cs=new Set(F.hooks.cards());
  for(const [c,v] of cardsV) if(!cs.has(c)){ v.dispose(); cardsV.delete(c); }
  for(const c of cs){ let v=cardsV.get(c); if(!v){ v=new CardV(c); cardsV.set(c,v); } v.update(); }
}
function resize(){
  const w=canvas.clientWidth, h=canvas.clientHeight; if(!w||!h) return;
  renderer.setSize(w,h,false); camera.aspect=w/h; camera.updateProjectionMatrix();
}
D3.render = dt => {
  if(!active||!ok) return;
  if(canvas.width!==Math.floor(canvas.clientWidth*renderer.getPixelRatio())) resize();
  sync(dt); updateFx(); updateLight(dt); updateProps();
  renderer.render(scene,camera);
};
D3.init = (cv,floor) => {
  F=floor; canvas=cv;
  try{
    renderer=new THREE.WebGLRenderer({canvas:cv,antialias:true});
  }catch(e){ console.warn('3D view needs WebGL',e); return false; }
  renderer.setPixelRatio(Math.min(2,window.devicePixelRatio||1)); renderer.shadowMap.enabled=true; renderer.shadowMap.type=THREE.PCFSoftShadowMap;
  scene=new THREE.Scene(); scene.fog=new THREE.Fog('#8cc4ee',700,1400);
  camera=new THREE.PerspectiveCamera(38,2,1,3000); applyCam();
  buildStatic(); buildLights(); setupControls();
  ok=true; return true;
};
D3.setActive = on => { active=on&&ok; if(active) resize(); };
D3.setupToggle = (btn,c2,c3,floor) => {
  const okInit=D3.init(c3,floor);
  let want='3d'; try{ want=localStorage.getItem('goatFloorView')||'3d'; }catch(e){}
  const show=v=>{
    const use3d=v==='3d'&&okInit;
    c3.style.display=use3d?'block':'none'; c2.style.display=use3d?'none':'block';
    floor.render2d=!use3d; D3.setActive(use3d);
    btn.textContent='View: '+(use3d?'3D':'2D'); btn.classList.toggle('on',use3d);
    try{ localStorage.setItem('goatFloorView',use3d?'3d':'2d'); }catch(e){}
  };
  if(!okInit){ btn.textContent='3D needs WebGL'; btn.disabled=true; }
  btn.addEventListener('click',()=>show(floor.render2d?'3d':'2d'));
  const prev=floor.hooks.after; floor.hooks.after=dt=>{ if(prev) prev(dt); D3.render(dt); };
  show(want);
};
D3.screenOf = (x,y,h) => { const v=P(x,y,h).project(camera), r=canvas.getBoundingClientRect(); return {x:r.left+(v.x+1)/2*r.width, y:r.top+(1-v.y)/2*r.height}; };
window.Floor3D = D3;
})();
