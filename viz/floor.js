/* G.O.A.T. AI floor: shared drawing, sprites, effects, lighting, weather and sound. Used by floor.html.
   Adapted from the Software Farm floor: the Test room is QA, and the Split / Roadmap room is the project manager's. */
(function(){
'use strict';
const W = 480, H = 224, S = 3, HALL_Y = 113;
const COL = {intake:'#5DCAA5',plan:'#7F77DD',qa:'#EF9F27',implement:'#378ADD',review:'#D4537E',pm:'#D85A30',retro:'#97C459',coord:'#888780',human:'#f4f1e8',other:'#B4B2A9'};
const SIZECOL = {XS:'#F1EFE8',S:'#C0DD97',M:'#FAC775',L:'#F0997B',XL:'#E24B4A'};
const NOSIZE = '#B4B2A9';
const SKINS = ['#f1c9a5','#d9a27a','#a86b45','#6e4428'];
const HAIRS = ['#2b1d14','#5a3a1e','#c9a24a','#1b1b1b','#8a3b22'];
const NAME = {intake:'Intake',plan:'Plan',qa:'QA',implement:'Implement',review:'Review',pm:'PM',retro:'Retro',coord:'Coordinator',human:'You',other:'Agent'};
// The rooms along the top. QA is not a stage (no card waits for it), but its room sits next to the dock it tests.
const STAGES = ['intake','plan','implement','review','qa'];
const roleKey = r => !r ? 'other' : (r.startsWith('retro') || r.includes('aggregate')) ? 'retro' : (COL[r] ? r : 'other');
const roleName = r => roleKey(r) === 'other' ? r : NAME[roleKey(r)];
const clamp = (v,a,b) => Math.max(a, Math.min(b, v));
function shade(hex,f){ const n=parseInt(hex.slice(1),16); const r=clamp(Math.round((n>>16)*f),0,255), g=clamp(Math.round((n>>8&255)*f),0,255), b=clamp(Math.round((n&255)*f),0,255); return '#'+((1<<24)|(r<<16)|(g<<8)|b).toString(16).slice(1); }
function mix(a,b,t){ const x=parseInt(a.slice(1),16), y=parseInt(b.slice(1),16); const c=k=>Math.round(((x>>k)&255)*(1-t)+((y>>k)&255)*t); return 'rgb('+c(16)+','+c(8)+','+c(0)+')'; }

const F = {W,H,S,HALL_Y,COL,SIZECOL,NOSIZE,NAME,STAGES,roleKey,roleName};

// ---------- rooms ----------
const R = {};
function room(id,name,x,y,w,h,floor,top){ R[id] = {id,name,x,y,w,h,floor,top,door:x+Math.floor(w/2)}; }
room('board','GitHub issues',4,6,66,92,'#3b3a46',true);
STAGES.forEach((r,i)=>room(r,NAME[r],70+i*64,6,64,92,'#4a4038',true));
room('dock','Shipping dock',390,6,86,92,'#33403a',true);
room('coord','Coordinator',4,128,72,92,'#3d3d42',false);
room('gate','Gate desk',76,128,80,92,'#2f3a4a',false);
room('lounge','Lounge',156,128,76,92,'#4a3346',false);
room('parking','Parking',232,128,80,92,'#38383c',false);
room('split','Project manager',312,128,76,92,'#4a3a2c',false);
room('library','Retro library',388,128,88,92,'#2f4031',false);
const ROOMS = Object.values(R);
const roomAt = (x,y) => ROOMS.find(r=>x>=r.x&&x<=r.x+r.w&&y>=r.y&&y<=r.y+r.h)||null;
F.R = R;

// ---------- slots ----------
class Slots{
  constructor(list,stand){ this.list=list; this.stand=stand; this.used=new Array(list.length).fill(null); this.extra=[]; }
  take(o){ const i=this.used.indexOf(null); if(i>=0){ this.used[i]=o; return this.list[i]; } this.extra.push(o); return this.list[this.list.length-1]; }
  free(o){ const i=this.used.indexOf(o);
    if(i>=0){ this.used[i]=null; if(this.extra.length){ const m=this.extra.shift(); this.used[i]=m; if(!(m instanceof Agent)){ m.x=this.list[i][0]; m.y=this.list[i][1]; } } return; }
    const j=this.extra.indexOf(o); if(j>=0) this.extra.splice(j,1); }
  items(){ return this.used.filter(Boolean).concat(this.extra); }
}
const grid = (x0,y0,cols,rows,dx,dy) => { const l=[]; for(let r=0;r<rows;r++) for(let c=0;c<cols;c++) l.push([x0+c*dx,y0+r*dy]); return l; };
F.wall = new Slots(grid(R.board.x+7,R.board.y+16,2,5,29,10),[R.board.x+40,R.board.y+84]);
F.seats = {}; F.trays = {};
for(const r of STAGES){ const {x,y}=R[r];
  F.seats[r]=new Slots([[x+17,y+52,x+14,y+31],[x+47,y+52,x+44,y+31]]);
  F.trays[r]=new Slots(grid(x+20,y+74,3,1,9,0),[x+32,y+86]); }
F.seats.split = new Slots([[R.split.x+26,R.split.y+60,R.split.x+23,R.split.y+41],[R.split.x+50,R.split.y+60,R.split.x+47,R.split.y+41]]);
F.seats.retro = new Slots([[R.library.x+36,R.library.y+76,R.library.x+33,R.library.y+57],[R.library.x+54,R.library.y+76,R.library.x+51,R.library.y+57]]);
F.seats.other = new Slots([[R.coord.x+56,R.coord.y+62,R.coord.x+53,R.coord.y+43]]);
F.seatsFor = role => { const k=roleKey(role); return F.seats[k] || (k==='pm'?F.seats.split:F.seats.other); };
F.splitTray = new Slots(grid(R.split.x+20,R.split.y+76,4,1,9,0),[R.split.x+38,R.split.y+70]);
F.gateIn = new Slots(grid(R.gate.x+19,R.gate.y+37,5,1,9,0),[R.gate.x+40,R.gate.y+26]);
F.lounge = new Slots(grid(R.lounge.x+14,R.lounge.y+56,3,1,22,0));
F.parking = new Slots(grid(R.parking.x+12,R.parking.y+30,3,2,22,20),[R.parking.x+40,R.parking.y+76]);
F.shelf = new Slots(grid(R.library.x+8,R.library.y+16,8,2,9,16),[R.library.x+44,R.library.y+50]);
F.dock = new Slots(grid(R.dock.x+10,R.dock.y+22,6,2,12,12),[R.dock.x+30,R.dock.y+80]);
F.SPAWN = [R.coord.door,R.coord.y+14];
F.FOREMAN_HOME = [R.coord.x+22,R.coord.y+62];
F.HUMAN_HOME = [R.gate.x+40,R.gate.y+62];
F.Slots = Slots;

// ---------- time ----------
F.T = 0; F.speed = 1; F.paused = false; F.timeMode = null;
const waiters = [];
F.wait = s => new Promise(r=>waiters.push({t:F.T+s,r}));
F.until = async fn => { while(!fn()) await F.wait(0.2); };
F.later = (s,fn) => { F.wait(s).then(fn); };

// ---------- agents ----------
const agents = []; let aid = 0;
F.agents = agents;
function route(a,tx,ty){
  const from=roomAt(a.x,a.y), to=roomAt(tx,ty), p=[];
  if(from&&to&&from===to){ p.push([tx,ty]); return p; }
  if(from){ p.push([from.door,from.top?from.y+from.h-3:from.y+3]); p.push([from.door,HALL_Y]); }
  if(to){ p.push([to.door,HALL_Y]); p.push([to.door,to.top?to.y+to.h-3:to.y+3]); }
  p.push([tx,ty]); return p;
}
class Agent{
  constructor(role,x,y,label){
    this.id=++aid; this.role=role; this.x=x; this.y=y; this.path=[]; this.state='idle'; this.label=label||'';
    this.speed=44; this.anim=0; this.card=null; this.seat=null; this.chain=Promise.resolve();
    this.slump=false; this.say=null; this.question=null; this.workStart=0; this.paperRate=1;
    const shirt=COL[roleKey(role)]||COL.other;
    this.pal={h:HAIRS[(this.id*3)%HAIRS.length], s:SKINS[(this.id*7)%SKINS.length], e:'#1b1b1b', c:shirt, d:shade(shirt,0.72),
      p:'#2b2b38', b:'#151518', w:'#f4f1e8', k:'#1b1b1b', y:'#EFC72E', o:'#E8862A', r:'#D33A3A', l:'#9fd3ef', n:'#7a5230', t:'#c9a26a', g:'#8a8a90'};
    if(role==='human'){ this.pal.c='#f4f1e8'; this.pal.d='#d8d4c8'; }
    agents.push(this);
  }
  goto(x,y){ this.path=route(this,x,y); this.state='walk'; return new Promise(r=>this.arrive=r); }
  sit(seat){ this.seat=seat; this.state='work'; this.workStart=F.T; }
  do(fn){ this.chain=this.chain.then(fn).catch(e=>console.error(e)); return this.chain; }
  update(dt){
    if(this.path.length){
      const [tx,ty]=this.path[0], dx=tx-this.x, dy=ty-this.y, d=Math.hypot(dx,dy), st=(this.slump?22:this.speed)*dt;
      if(d<=st){ this.x=tx; this.y=ty; this.path.shift(); if(!this.path.length){ this.state='idle'; const r=this.arrive; this.arrive=null; r&&r(); } }
      else { this.x+=dx/d*st; this.y+=dy/d*st; }
      this.anim+=dt*(this.slump?5:8);
    } else this.anim+=dt*5;
  }
  remove(){ const i=agents.indexOf(this); if(i>=0) agents.splice(i,1); }
}
F.Agent = Agent;
F.leave = async a => { await a.goto(...F.SPAWN); a.remove(); };
F.say = (a,text,secs) => { a.say={text,until:F.T+secs}; };

// ---------- cards ----------
F.place = (c,slots) => { const p=slots.take(c); c.x=p[0]; c.y=p[1]; c.slots=slots; c.holder=null; };
F.pick = (a,c) => { if(c.slots) c.slots.free(c); c.slots=null; c.holder=a; a.card=c; };
F.drop = (a,c,slots) => { a.card=null; F.place(c,slots); };
F.stand = c => c.slots&&c.slots.stand ? c.slots.stand : [c.x+3,c.y+12];
F.cardPos = c => {
  if(c.holder){ const h=c.holder; if(h.state==='work'&&h.seat) return [h.seat[2],h.seat[3]]; return [Math.round(h.x)-3,Math.round(h.y)-8]; }
  return [c.x,c.y];
};

// ---------- sound ----------
const SND = {on:false, ac:null, last:{}};
function tone(f,dur,o){ o=o||{}; const ac=SND.ac, t=ac.currentTime+(o.at||0), osc=ac.createOscillator(), g=ac.createGain();
  osc.type=o.type||'square'; osc.frequency.setValueAtTime(f,t); if(o.slide) osc.frequency.linearRampToValueAtTime(o.slide,t+dur);
  g.gain.setValueAtTime(o.vol||0.04,t); g.gain.exponentialRampToValueAtTime(0.0001,t+dur);
  osc.connect(g); g.connect(ac.destination); osc.start(t); osc.stop(t+dur+0.03); }
function noise(dur,vol,at,hp){ const ac=SND.ac, n=Math.max(1,Math.floor(ac.sampleRate*dur)), buf=ac.createBuffer(1,n,ac.sampleRate), d=buf.getChannelData(0);
  for(let i=0;i<n;i++) d[i]=(Math.random()*2-1)*(1-i/n);
  const s=ac.createBufferSource(), f=ac.createBiquadFilter(), g=ac.createGain(); s.buffer=buf; f.type='highpass'; f.frequency.value=hp||2000; g.gain.value=vol;
  s.connect(f); f.connect(g); g.connect(ac.destination); s.start(ac.currentTime+(at||0)); }
F.sound = {
  get on(){ return SND.on; },
  toggle(){ SND.on=!SND.on; if(SND.on){ if(!SND.ac){ const C=window.AudioContext||window.webkitAudioContext; if(C) SND.ac=new C(); } if(SND.ac) SND.ac.resume(); } return SND.on; },
  play(name){
    if(!SND.on||!SND.ac) return;
    const now=performance.now(); if(name!=='click'&&SND.last[name]&&now-SND.last[name]<120) return; SND.last[name]=now;
    switch(name){
      case 'click': noise(0.012,0.05,0,3000); break;
      case 'chime': tone(988,0.15,{type:'sine',vol:0.06}); tone(1319,0.28,{type:'sine',vol:0.06,at:0.13}); break;
      case 'cash': tone(1568,0.08,{vol:0.035}); tone(2093,0.3,{vol:0.035,at:0.08}); noise(0.15,0.04,0.02,4000); break;
      case 'stamp': tone(120,0.16,{type:'sine',vol:0.18,slide:55}); noise(0.05,0.08,0,700); break;
      case 'bell': tone(1046,0.9,{type:'sine',vol:0.06}); tone(2093,0.5,{type:'sine',vol:0.025}); break;
      case 'honk': tone(233,0.22,{vol:0.035}); tone(294,0.22,{vol:0.03}); break;
      case 'sigh': tone(330,0.5,{type:'triangle',vol:0.05,slide:170}); break;
      case 'error': tone(110,0.3,{vol:0.05}); tone(92,0.4,{vol:0.05,at:0.18}); break;
    }
  }
};

// ---------- effects ----------
const fx = [];
const CONF = ['#E24B4A','#EFC72E','#5DCAA5','#378ADD','#D4537E','#f4f1e8'];
const truck = {honk:-99};
F.bellT = -99;
F.bugs = [];
F.fx = fx; F.truck = truck; F.ROOMS = ROOMS; F.shade = shade; F.render2d = true;
F.stamp = (text,color,x,y) => { fx.push({kind:'stamp',text,color:color||'#D33A3A',x:x||R.gate.x+40,y:y||R.gate.y+24,t0:F.T,dur:1.9}); F.sound.play('stamp'); };
F.confetti = (x,y) => { const parts=[]; for(let i=0;i<46;i++) parts.push({vx:(Math.random()-0.5)*70,vy:-25-Math.random()*45,c:CONF[i%CONF.length]}); fx.push({kind:'confetti',x,y,parts,t0:F.T,dur:2.4}); };
F.merge = () => { const d=R.dock; F.confetti(d.x+40,d.y+56); truck.honk=F.T; F.stamp('MERGED','#3B9E52',d.x+40,d.y+40); F.sound.play('cash'); F.later(0.35,()=>F.sound.play('honk')); };
F.smoke = (x,y,dur) => { fx.push({kind:'smoke',x,y,t0:F.T,dur:dur||6}); };
F.addBug = label => { F.bugs.push({label,t0:F.T}); if(F.bugs.length>5) F.bugs.shift(); F.sound.play('error'); };
F.ringBell = () => { F.bellT=F.T; F.sound.play('bell'); };

// ---------- sprites ----------
const FRONT = [
  '...hhhh...',
  '..hhhhhh..',
  '..hssssh..',
  '..sesses..',
  '..ssssss..',
  '...ssss...',
  '..cccccc..',
  '.cccccccc.',
  '.cccccccc.',
  '.sccccccs.',
  '..dddddd..'];
const LEGS_STAND = ['..pppppp..','..pp..pp..','..pp..pp..','..bb..bb..'];
const LEGS_A = ['..pppppp..','..pp..pp..','.pp....pp.','.bb....bb.'];
const LEGS_B = ['..pppppp..','...pppp...','...pppp...','...bbbb...'];
const BACK = [
  '...hhhh...',
  '..hhhhhh..',
  '..hhhhhh..',
  '..hhhhhh..',
  '..shhhhs..',
  '...ssss...',
  '..cccccc..',
  '.cccccccc.',
  '.cccccccc.',
  '.cccccccc.',
  '..dddddd..'];
const ACC_FRONT = {
  intake:[[0,'..dddddd..'],[1,'..dddddddd']],
  plan:[[3,'..kekkek..']],
  qa:[[6,'.........kk.'],[7,'........kllk'],[8,'........kllk'],[9,'........nkk.'],[10,'.......n....']],
  implement:[[0,'..dddddd..'],[1,'.dhhhhhhd.'],[2,'.d......d.'],[3,'.d......d.'],[4,'.d......d.'],[8,'........ww'],[9,'........wn']],
  review:[[1,'.........r'],[2,'........r.'],[7,'.nnn......'],[8,'.www......'],[9,'.wrw......'],[10,'.www......']],
  pm:[[6,'.yyyyyyy..'],[7,'.....yy...'],[8,'.....yy...']],
  retro:[[-1,'....hh....'],[3,'..kekkek..'],[7,'.t........'],[8,'.t........']],
  coord:[[-1,'...yyyy...'],[0,'..yyyyyy..'],[7,'.o......o.'],[8,'.oyyyyyyo.']],
  human:[[6,'....rr....'],[7,'....rr....'],[8,'....rr....'],[9,'.....r....']]
};
const ACC_BACK = {
  intake:[[0,'..dddddd..']],
  implement:[[0,'...dddd...'],[1,'..dddddd..'],[2,'..dddddd..'],[3,'..dddddd..'],[4,'..dddddd..']],
  retro:[[-1,'....hh....']],
  coord:[[-1,'...yyyy...'],[0,'..yyyyyy..']],
  pm:[[6,'.yyyyyyy..']]
};
let ctx = null, bctx = null, bg = null, canvas = null;
function px(g,x,y,w,h,c){ g.fillStyle=c; g.fillRect(x,y,w,h); }
function txt(g,s,x,y,c,size,align){ g.font=(size||5)+'px ui-monospace,Menlo,monospace'; g.fillStyle=c||'#e8e6df'; g.textAlign=align||'left'; g.textBaseline='top'; g.fillText(s,x,y); }
F.px = (x,y,w,h,c) => px(ctx,x,y,w,h,c);
F.txt = (s,x,y,c,size,align) => txt(ctx,s,x,y,c,size,align);
function drawRows(rows,ox,oy,pal){ for(let r=0;r<rows.length;r++){ const line=rows[r]; for(let i=0;i<line.length;i++){ const ch=line[i]; if(ch==='.') continue; const col=pal[ch]; if(col) px(ctx,ox+i,oy+r,1,1,col); } } }
function drawAcc(list,ox,oy,pal,headDrop){ if(!list) return; for(const [row,line] of list) drawRows([line],ox,oy+row+(row<6?headDrop:0),pal); }

function drawAgent(a,sel){
  const x=Math.round(a.x), y=Math.round(a.y), k=roleKey(a.role)==='other'&&a.role!=='coord'&&a.role!=='human'?'other':(a.role==='coord'||a.role==='human'?a.role:roleKey(a.role)), pal=a.pal;
  if(a.state==='sleep'){
    px(ctx,x+2,y-6,7,4,'#f4f1e8');
    px(ctx,x+3,y-7,5,5,pal.s); px(ctx,x+3,y-8,5,2,pal.h); px(ctx,x+7,y-7,1,4,pal.h);
    px(ctx,x+4,y-5,1,1,pal.k); px(ctx,x+5,y-5,1,1,pal.k);
    px(ctx,x-9,y-6,12,5,pal.c); px(ctx,x-9,y-6,12,1,pal.d); px(ctx,x-10,y-3,2,2,pal.b);
    txt(ctx,'z',x+8,y-13-((F.T*1.2)%1)*5,'#c9c2e8',5); return;
  }
  const ox=x-5, oy=y-14;
  px(ctx,x-4,y+1,9,1,'rgba(0,0,0,0.35)');
  if(a.state==='work'){
    const f=Math.floor(a.anim)%2;
    drawRows(BACK,ox,oy,pal); drawAcc(ACC_BACK[k],ox,oy,pal,0);
    px(ctx,ox,oy+7+f,1,2,pal.c); px(ctx,ox+9,oy+8-f,1,2,pal.c);
    px(ctx,x-4,y-4,9,5,'#3a2a20'); px(ctx,x-4,y-4,9,1,'#5a4030');
  } else {
    const walk=a.state==='walk', f=walk?Math.floor(a.anim)%2:-1, hd=a.slump?1:0;
    drawRows(FRONT.slice(6),ox,oy+6,pal);
    drawRows(FRONT.slice(0,6),ox,oy+hd,pal);
    drawRows(f<0?LEGS_STAND:f?LEGS_A:LEGS_B,ox,oy+11,pal);
    drawAcc(ACC_FRONT[k],ox,oy,pal,hd);
    if(a.slump){ px(ctx,ox+3,oy+3+hd,1,1,pal.s); px(ctx,ox+6,oy+3+hd,1,1,pal.s); px(ctx,ox+3,oy+4+hd,1,1,pal.k); px(ctx,ox+6,oy+4+hd,1,1,pal.k); px(ctx,ox+9,oy+1,1,2,'#9fd3ef'); }
  }
  if(sel){ ctx.strokeStyle='#85B7EB'; ctx.lineWidth=0.6; ctx.strokeRect(x-7,y-17,14,19); }
}

const ICONS = {
  note:['wwww.','wkkw.','wwww.','wkkw.','wwww.'],
  bulb:['.yyy.','yyyyy','yyyyy','.ggg.','..g..'],
  bug:['k.k.k','.rrr.','krrrk','.rrr.','k...k'],
  check:['....G','...G.','G.G..','.G...','.....'],
  eye:['.....','.www.','wwkww','.www.','.....'],
  scissors:['k...k','.k.k.','..k..','.r.r.','r...r'],
  book:['rrrr.','rwwr.','rrrr.','rwwr.','rrrr.'],
  flag:['k....','kyyy.','kyy..','k....','k....'],
  gear:['.g.g.','ggggg','.g.g.','ggggg','.g.g.']
};
const ICON_PAL = {w:'#f4f1e8',k:'#1b1b1b',y:'#EFC72E',g:'#8a8a90',r:'#D33A3A',G:'#3B9E52'};
function moodIcon(a){
  switch(roleKey(a.role)){
    case 'intake': return 'note'; case 'plan': return 'bulb'; case 'qa': return (Math.floor((F.T+a.id)/6)%2)?'check':'bug';
    case 'implement': return 'code'; case 'review': return 'eye'; case 'pm': return 'flag';
    case 'retro': return 'book'; case 'human': return 'note'; default: return 'gear';
  }
}
F.ICONS = ICONS; F.ICON_PAL = ICON_PAL; F.moodIcon = a => moodIcon(a);
function bubbleBox(x,y,w,h){ px(ctx,x,y,w,h,'#f4f1e8'); px(ctx,x,y+h,w,1,'#b8b4a8'); }
function drawMood(a){
  if(a.state!=='work') return;
  if(((F.T+a.id*1.7)%6)>2) return;
  const x=Math.round(a.x)+5, y=Math.round(a.y)-28;
  bubbleBox(x,y,9,8); px(ctx,x+1,y+8,2,2,'#f4f1e8');
  const icon=moodIcon(a);
  if(icon==='code') txt(ctx,'</>',x+4.5,y+2,'#378ADD',4,'center');
  else { const m=ICONS[icon]; for(let r=0;r<m.length;r++) for(let i=0;i<m[r].length;i++){ const c=ICON_PAL[m[r][i]]; if(c) px(ctx,x+2+i,y+1+r,1,1,c); } }
}
function wrap(text,max){ const words=String(text).split(/\s+/); const lines=[]; let cur='';
  for(const w of words){ if((cur+' '+w).trim().length>max){ if(cur) lines.push(cur); cur=w.slice(0,max); } else cur=(cur+' '+w).trim(); if(lines.length>=3) break; }
  if(cur&&lines.length<3) lines.push(cur); if(lines.length===3&&words.join(' ').length>lines.join(' ').length) lines[2]=lines[2].slice(0,max-1)+'…'; return lines; }
F.wrapText = wrap;
function drawSpeech(a,text){
  const lines=wrap(text,26), w=Math.max(...lines.map(l=>l.length))*2.45+5, h=lines.length*5+3;
  const ax=Math.round(a.x), top=a.state==='sleep'?Math.round(a.y)-22-h:Math.round(a.y)-30-h;
  const x=clamp(ax-w/2,2,W-w-2);
  bubbleBox(x,top,w,h); px(ctx,clamp(ax-1,x+1,x+w-3),top+h,2,2,'#f4f1e8');
  lines.forEach((l,i)=>txt(ctx,l,x+2.5,top+1.6+i*5,'#26215C',4));
}

// ---------- background ----------
function desk(g,x,y,w){ px(g,x,y,w,8,'#7a5230'); px(g,x,y,w,2,'#9a6a40'); px(g,x+1,y+8,2,3,'#4a3220'); px(g,x+w-3,y+8,2,3,'#4a3220'); }
function monitor(g,x,y){ px(g,x,y-7,9,6,'#1e1e24'); px(g,x+1,y-6,7,4,'#2f5a74'); px(g,x+4,y-1,2,1,'#1e1e24'); }
function plant(g,x,y){ px(g,x,y,5,4,'#3b6d11'); px(g,x+1,y-2,3,2,'#639922'); px(g,x+1,y+4,3,3,'#993c1d'); }
function tray(g,x,y,w){ px(g,x,y,w,7,'#3a3028'); px(g,x+1,y+1,w-2,5,'#5a4a3a'); }
const WINDOWS = [];
function drawBackground(){
  const g=bctx; g.setTransform(S,0,0,S,0,0);
  px(g,0,0,W,H,'#16161c'); px(g,4,98,472,30,'#5b574d');
  for(let x=4;x<476;x+=8) px(g,x,112,4,1,'#6b675c');
  for(const r of ROOMS){ px(g,r.x,r.y,r.w,r.h,r.floor);
    for(let ty=0;ty<r.h;ty+=8) for(let tx=0;tx<r.w;tx+=8) if(((tx+ty)/8)%2) px(g,r.x+tx,r.y+ty,Math.min(8,r.w-tx),Math.min(8,r.h-ty),'rgba(255,255,255,0.035)'); }
  for(const r of ROOMS){ const wc='#1a1a20';
    px(g,r.x,r.y,r.w,2,wc); px(g,r.x,r.y+r.h-2,r.w,2,wc); px(g,r.x,r.y,2,r.h,wc); px(g,r.x+r.w-2,r.y,2,r.h,wc);
    if(r.top) px(g,r.door-6,r.y+r.h-2,12,2,'#5b574d'); else px(g,r.door-6,r.y,12,2,'#5b574d'); }
  const b=R.board; px(g,b.x+4,b.y+12,58,62,'#5a4020'); px(g,b.x+5,b.y+13,56,60,'#8a6a3a'); px(g,b.x+5,b.y+62,56,1,'#6a4a28');
  for(const r of STAGES){ const {x,y}=R[r];
    desk(g,x+6,y+30,22); desk(g,x+36,y+30,22); monitor(g,x+8,y+30); monitor(g,x+38,y+30);
    tray(g,x+18,y+73,28); px(g,x+4,y+12,56,3,COL[r]); plant(g,x+54,y+18); }
  const d=R.dock; for(let i=0;i<2;i++){ px(g,d.x+6,d.y+52+i*11,16,10,'#9a7a4a'); px(g,d.x+6,d.y+56+i*11,16,1,'#6a5030'); }
  px(g,d.x+2,d.y+48,d.w-4,1,'#4a5a50'); for(let i=0;i<6;i++) px(g,d.x+30+i*8,d.y+86,4,2,'#EFC72E');
  const co=R.coord; desk(g,co.x+8,co.y+40,30); monitor(g,co.x+12,co.y+40); monitor(g,co.x+24,co.y+40); desk(g,co.x+46,co.y+40,20);
  const ga=R.gate; desk(g,ga.x+16,ga.y+36,48); plant(g,ga.x+68,ga.y+20);
  const lo=R.lounge; px(g,lo.x+6,lo.y+46,64,4,'#5a2f4c'); px(g,lo.x+6,lo.y+50,64,10,'#6a3a5a'); px(g,lo.x+22,lo.y+72,30,8,'#7a5230'); px(g,lo.x+30,lo.y+70,4,2,'#f4f1e8'); plant(g,lo.x+64,lo.y+74);
  const pa=R.parking; for(let i=0;i<4;i++) px(g,pa.x+8+i*22,pa.y+24,1,44,'#cfcbbf');
  const sp=R.split; desk(g,sp.x+12,sp.y+40,52); tray(g,sp.x+18,sp.y+75,38);
  const li=R.library; const books=['#7F77DD','#D85A30','#1D9E75','#EF9F27','#D4537E','#378ADD'];
  for(let row=0;row<2;row++) px(g,li.x+6,li.y+24+row*16,76,2,'#6a4a2a');
  desk(g,li.x+30,li.y+56,32); px(g,li.x+72,li.y+60,8,14,'#6a4a2a'); for(let i=0;i<6;i++) px(g,li.x+73,li.y+61+i*2,6,1,books[i]);
  for(const r of ROOMS){ const w=Math.min(r.w-4,r.name.length*3+4); px(g,r.x+2,r.y+2,w,7,'rgba(0,0,0,0.4)'); txt(g,r.name,r.x+4,r.y+3,'#e8e6df',5); }
  WINDOWS.length=0;
  for(const r of ROOMS) if(r.top) WINDOWS.push([r.x+r.w-22,r.y+3,16,7]);
}

// ---------- lighting and weather ----------
function daylight(){
  if(F.timeMode==='day') return {night:0,dusk:0};
  if(F.timeMode==='dusk') return {night:0.45,dusk:1};
  if(F.timeMode==='night') return {night:1,dusk:0};
  const d=new Date(), h=d.getHours()+d.getMinutes()/60;
  const night = (h<5.5||h>=20.5) ? 1 : h<7 ? (7-h)/1.5 : h>=18 ? (h-18)/2.5 : 0;
  const dusk = (h>=17&&h<20.5) ? 1-Math.abs(h-18.75)/1.75 : (h>=5.5&&h<7.5) ? 1-Math.abs(h-6.5) : 0;
  return {night:clamp(night,0,1), dusk:clamp(dusk,0,1)};
}
F.daylight = daylight;
function drawWindows(light,waiting){
  let sky=mix('#8cc4ee','#121a36',light.night); if(light.dusk>0) sky=mix(sky.startsWith('#')?sky:rgbToHex(sky),'#f0a070',light.dusk*0.55);
  for(const [x,y,w,h] of WINDOWS){
    px(ctx,x-1,y-1,w+2,h+2,'#2a2a30');
    ctx.save(); ctx.beginPath(); ctx.rect(x,y,w,h); ctx.clip();
    px(ctx,x,y,w,h,sky);
    if(waiting===0){
      if(light.night<0.5){ px(ctx,x+3,y+2,3,3,'#FFE27A'); px(ctx,x+2,y+3,5,1,'#FFE27A'); }
      else { px(ctx,x+11,y+1,2,2,'#f4f1e8'); px(ctx,x+3,y+2,1,1,'#f4f1e8'); px(ctx,x+7,y+5,1,1,'#f4f1e8'); }
    } else {
      const cloud = waiting>=3 ? '#7d7f8c' : '#d0d2da';
      for(let i=0;i<2;i++){ const cx=x+((F.T*3+i*11+x)%(w+10))-6; px(ctx,cx,y+1+i*2,7,2,cloud); px(ctx,cx+1,y+i*2,4,1,cloud); }
      if(waiting>=3) for(let k=0;k<6;k++){ const rx=x+((k*5+F.T*14)%w), ry=y+((k*3+F.T*40)%h); px(ctx,rx,ry,1,2,'#9fc3e8'); }
    }
    ctx.restore();
    px(ctx,x+w/2,y,1,h,'#2a2a30');
  }
}
function rgbToHex(rgb){ const m=rgb.match(/\d+/g); return '#'+m.map(v=>(+v).toString(16).padStart(2,'0')).join(''); }
function drawNight(light){
  if(light.night<=0.01) return;
  px(ctx,0,0,W,H,'rgba(12,16,44,'+(0.55*light.night)+')');
  ctx.save(); ctx.globalCompositeOperation='lighter';
  const lamp=(x,y,r,a)=>{ const g=ctx.createRadialGradient(x,y,0,x,y,r); g.addColorStop(0,'rgba(255,190,110,'+a+')'); g.addColorStop(1,'rgba(255,190,110,0)'); ctx.fillStyle=g; ctx.fillRect(x-r,y-r,r*2,r*2); };
  for(const a of agents) if(a.state==='work'&&a.seat) lamp(a.seat[2]+3,a.seat[3]-3,16,0.32*light.night);
  lamp(R.coord.x+22,R.coord.y+38,14,0.22*light.night);
  lamp(R.gate.x+40,R.gate.y+36,16,0.22*light.night);
  for(const r of ROOMS) lamp(r.door,r.top?r.y+r.h-2:r.y+2,8,0.12*light.night);
  ctx.restore();
}

// ---------- dynamic props ----------
function drawTruck(){
  const d=R.dock, x=d.x+50, y=d.y+58; const age=F.T-truck.honk; const bob=age<0.9?-(Math.floor(age*12)%2):0;
  px(ctx,x,y+bob,24,12,'#e8e6df'); px(ctx,x,y+bob,24,2,'#c8c4b8'); px(ctx,x+24,y+3+bob,8,9,'#D85A30'); px(ctx,x+26,y+5+bob,4,3,'#9fd3ef');
  px(ctx,x+3,y+12,5,3,'#1b1b1b'); px(ctx,x+17,y+12,5,3,'#1b1b1b'); px(ctx,x+25,y+12,5,3,'#1b1b1b');
  txt(ctx,'GOAT',x+4,y+4+bob,'#3B9E52',5);
  if(age<0.9) txt(ctx,'HONK',x+16,y-8,'#EFC72E',5,'center');
}
function drawClock(){
  const co=R.coord, cx=co.x+60, cy=co.y+18, now=new Date(), h=now.getHours()%12+now.getMinutes()/60, m=now.getMinutes()+now.getSeconds()/60;
  ctx.beginPath(); ctx.arc(cx,cy,6,0,Math.PI*2); ctx.fillStyle='#e8e6df'; ctx.fill(); ctx.lineWidth=1; ctx.strokeStyle='#2a2a30'; ctx.stroke();
  const hand=(ang,len,w)=>{ ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(cx+Math.sin(ang)*len,cy-Math.cos(ang)*len); ctx.lineWidth=w; ctx.strokeStyle='#1b1b1b'; ctx.stroke(); };
  hand(h/12*Math.PI*2,3.2,0.9); hand(m/60*Math.PI*2,4.8,0.6);
  const age=F.T-F.bellT, shake=age<1.3?(Math.floor(age*16)%2?1:-1):0, bx=co.x+47+shake, by=co.y+12;
  px(ctx,bx+1,by,3,1,'#EFC72E'); px(ctx,bx,by+1,5,3,'#EFC72E'); px(ctx,bx-1,by+4,7,1,'#c99a1e'); px(ctx,bx+2,by+5,1,1,'#8a6a10');
  if(age<1.3) txt(ctx,'ding',bx+2,by-7,'#EFC72E',4,'center');
}
function drawScoreboard(lines){
  const co=R.coord, x=co.x+4, y=co.y+66;
  px(ctx,x-1,y-1,66,27,'#6a4a2a'); px(ctx,x,y,64,25,'#1d2a22');
  (lines||[]).slice(0,4).forEach((l,i)=>txt(ctx,l,x+2,y+1.5+i*6,'#d8f0c8',4));
}
function drawPapers(a){
  if(a.state!=='work'||!a.seat) return;
  const n=Math.min(8,Math.floor((F.T-a.workStart)/(a.paperRate||1))+1), x=a.seat[2]+9, y=a.seat[3]+3;
  for(let i=0;i<n;i++){ px(ctx,x,y-i,5,1,i%2?'#e8e6df':'#f4f1e8'); }
  px(ctx,x+1,y-n+1,3,1,'#b8b4a8');
}
function drawBugs(){
  const b=R.board;
  F.bugs.forEach((bug,i)=>{ const x=b.x+7+i*11, y=b.y+64; px(ctx,x,y,9,7,'#E24B4A'); px(ctx,x,y,9,1,'#F09595');
    const m=ICONS.bug; for(let r=0;r<5;r++) for(let k=0;k<5;k++){ const c=m[r][k]; if(c==='k') px(ctx,x+2+k,y+1+r,1,1,'#1b1b1b'); else if(c==='r') px(ctx,x+2+k,y+1+r,1,1,'#f4f1e8'); } });
}
function drawFx(){
  for(let i=fx.length-1;i>=0;i--){ const e=fx[i], age=F.T-e.t0; if(age>e.dur){ fx.splice(i,1); continue; }
    if(e.kind==='confetti'){ for(const p of e.parts){ const x=e.x+p.vx*age, y=e.y+p.vy*age+55*age*age; if(y<H) px(ctx,Math.round(x),Math.round(y),2,1+((age*10|0)%2),p.c); } }
    else if(e.kind==='smoke'){ for(let k=0;k<7;k++){ const a=((age+k*0.25)%1.6), al=Math.max(0,0.75-a/1.6)*(age>e.dur-1?(e.dur-age):1);
        px(ctx,Math.round(e.x+Math.sin(k*1.7+a*4)*2),Math.round(e.y-a*14),3,2,'rgba(120,120,130,'+al.toFixed(2)+')'); }
      if(age<1.2) px(ctx,e.x-1,e.y+2,4,2,'#E24B4A'); }
    else if(e.kind==='stamp'){ const sc=age<0.15?2.2-1.2*(age/0.15):1, al=age>e.dur-0.5?(e.dur-age)/0.5:1;
      ctx.save(); ctx.globalAlpha=Math.max(0,al); ctx.translate(e.x,e.y); ctx.rotate(-0.16); ctx.scale(sc,sc);
      const w=e.text.length*4.3+8; ctx.lineWidth=1.2; ctx.strokeStyle=e.color; ctx.strokeRect(-w/2,-6,w,12);
      ctx.font='bold 7px ui-monospace,Menlo,monospace'; ctx.fillStyle=e.color; ctx.textAlign='center'; ctx.textBaseline='middle'; ctx.fillText(e.text,0,0.5);
      ctx.restore(); }
  }
}
function label(s,x,y,c){ ctx.font='4px ui-monospace,Menlo,monospace'; const w=ctx.measureText(s).width+3; px(ctx,x-w/2,y,w,6,'rgba(10,10,14,0.78)'); txt(ctx,s,x,y+0.8,c||'#e8e6df',4,'center'); }
function drawCard(c,x,y,sel){
  if(c.slots===F.shelf&&!c.holder){
    const col=SIZECOL[c.size]||NOSIZE; px(ctx,x,y,5,8,col); px(ctx,x,y+2,5,1,shade(col.startsWith('#')?col:'#B4B2A9',0.6)); px(ctx,x,y+5,5,1,shade(col,0.6)); px(ctx,x+4,y,1,8,'rgba(0,0,0,0.25)');
  } else {
    px(ctx,x,y,7,5,SIZECOL[c.size]||NOSIZE); px(ctx,x,y,7,1,'rgba(255,255,255,0.6)'); px(ctx,x+1,y+2,5,1,'rgba(0,0,0,0.35)');
    if(c.pr) px(ctx,x+5,y+3,2,2,'#1D9E75');
    if(c.elsewhere){ px(ctx,x-1,y-3,2,2,'#c9c2e8'); px(ctx,x+2,y-3,2,2,'#c9c2e8'); }
  }
  if(c.attention){ ctx.strokeStyle='rgba(250,199,117,'+(0.5+0.4*Math.sin(F.T*5)).toFixed(2)+')'; ctx.lineWidth=0.6; ctx.strokeRect(x-0.5,y-0.5,8,6); }
  if(sel){ ctx.strokeStyle='#85B7EB'; ctx.lineWidth=0.6; ctx.strokeRect(x-1,y-1,9,c.slots===F.shelf?10:7); }
}
function pileCount(slots){ if(slots.extra.length){ const p=slots.list[slots.list.length-1]; txt(ctx,'+'+slots.extra.length,p[0]+8,p[1]+1,'#f4ecd8',4); } }

// ---------- main draw ----------
F.hooks = {cards:()=>[], scoreLines:()=>[], drawUnder:null, drawOver:null, onSelect:null, onFrame:null};
F.sel = null;
function draw(){
  const light=daylight(), cards=Array.from(F.hooks.cards()), waiting=F.gateIn.items().length;
  ctx.setTransform(1,0,0,1,0,0); ctx.drawImage(bg,0,0);
  ctx.setTransform(S,0,0,S,0,0);
  drawWindows(light,waiting);
  drawTruck();
  if(waiting){ const p=0.35+0.35*Math.sin(F.T*4); ctx.strokeStyle='rgba(239,159,39,'+p.toFixed(2)+')'; ctx.lineWidth=1; ctx.strokeRect(R.gate.x+15,R.gate.y+35,50,10); }
  if(F.hooks.drawUnder) F.hooks.drawUnder(ctx);
  for(const c of cards) if(!c.holder){ drawCard(c,c.x,c.y,F.sel&&F.sel.o===c); if(c.slots===F.wall&&F.wall.used.includes(c)) txt(ctx,String(c.label).slice(0,8),c.x+9,c.y,'#f4ecd8',4); }
  [F.wall,F.gateIn,F.parking,F.shelf,F.dock,F.splitTray].concat(Object.values(F.trays)).forEach(pileCount);
  drawBugs();
  const list=agents.slice().sort((a,b)=>a.y-b.y);
  for(const a of list) drawPapers(a);
  for(const a of list) drawAgent(a,F.sel&&F.sel.o===a);
  for(const c of cards) if(c.holder){ const [x,y]=F.cardPos(c); drawCard(c,x,y,F.sel&&F.sel.o===c); }
  drawNight(light);
  drawClock();
  drawScoreboard(F.hooks.scoreLines());
  if(waiting) txt(ctx,waiting+' waiting for you',R.gate.x+5,R.gate.y+82,'#FAC775',4);
  for(const a of list) if(a.label) label(a.label,Math.round(a.x),Math.round(a.y)-(a.state==='sleep'?15:23),COL[roleKey(a.role)]||COL.other);
  for(const a of list){
    if(a.say&&a.say.until>F.T) drawSpeech(a,a.say.text);
    else if(a.state==='sleep'&&a.question) drawSpeech(a,a.question);
    else drawMood(a);
  }
  drawFx();
  if(F.hooks.drawOver) F.hooks.drawOver(ctx);
}

// ---------- loop and input ----------
F.init = (cv,hooks) => {
  canvas=cv; Object.assign(F.hooks,hooks||{});
  cv.width=W*S; cv.height=H*S; ctx=cv.getContext('2d');
  bg=document.createElement('canvas'); bg.width=W*S; bg.height=H*S; bctx=bg.getContext('2d');
  drawBackground();
  F.human = new Agent('human',...F.HUMAN_HOME,'You'); F.human.home=F.HUMAN_HOME;
  F.foreman = new Agent('coord',...F.FOREMAN_HOME,'Coord'); F.foreman.home=F.FOREMAN_HOME;
  cv.addEventListener('click',e=>{
    const r=cv.getBoundingClientRect(), x=(e.clientX-r.left)/r.width*W, y=(e.clientY-r.top)/r.height*H;
    const a=agents.find(a=>Math.abs(a.x-x)<7&&y<a.y+2&&y>a.y-18);
    if(a) F.sel={type:'agent',o:a};
    else { const c=Array.from(F.hooks.cards()).find(c=>{ const [cx,cy]=F.cardPos(c); return x>=cx-2&&x<=cx+9&&y>=cy-2&&y<=cy+10; }); F.sel=c?{type:'card',o:c}:null; }
    if(F.hooks.onSelect) F.hooks.onSelect(F.sel);
  });
  let last=performance.now();
  function frame(now){
    const dt=Math.min(0.05,(now-last)/1000); last=now;
    if(!F.paused){
      const sdt=dt*F.speed; F.T+=sdt;
      for(const a of agents.slice()) a.update(sdt);
      for(let i=waiters.length-1;i>=0;i--) if(waiters[i].t<=F.T){ const w=waiters.splice(i,1)[0]; w.r(); }
      const h=F.human; if(h.state!=='walk'&&!h.path.length) h.state=(F.gateIn.items().length&&!h.card)?'work':'idle';
      if(SND.on){ const typing=agents.filter(a=>a.state==='work'&&a.role!=='human').length; if(Math.random()<typing*dt*4) F.sound.play('click'); }
      if(F.hooks.onFrame) F.hooks.onFrame(sdt);
    }
    if(F.render2d) draw();
    if(F.hooks.after) F.hooks.after(dt);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
};
F.legendHTML = () =>
  ['intake','plan','implement','review','qa','pm','retro','coord'].map(r=>'<span><span class="sw" style="background:'+COL[r]+'"></span>'+NAME[r]+'</span>').join('')+
  '<span>&nbsp;Cards by size:</span>'+Object.keys(SIZECOL).map(s=>'<span><span class="sw" style="background:'+SIZECOL[s]+'"></span>'+s+'</span>').join('')+
  '<span><span class="sw" style="background:#1D9E75"></span>has a PR</span>';
window.Floor = F;
})();
