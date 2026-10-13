(() => {
  'use strict';
  const stage=document.getElementById('ag-touch-stage');
  if(!stage||stage.dataset.touchInitialized)return;
  const art=stage.querySelector('.ag-touch-art'),image=art?.querySelector('img');
  const canvas=document.getElementById('ag-touch-light'),status=document.getElementById('ag-touch-status');
  if(!art||!image||!canvas)return;
  const gl=canvas.getContext('webgl',{alpha:true,antialias:true,premultipliedAlpha:false});
  if(!gl)return;
  stage.dataset.touchInitialized='true';
  const W=1774,H=887,DEG=Math.PI/180,reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const ceramic=stage.dataset.material==='ceramic';
  const aiTip=ceramic?[939,358]:[951,355],robotTip=ceramic?[869,358]:[865,355];
  const clamp=(n,lo=0,hi=1)=>Math.max(lo,Math.min(hi,n));
  const smooth=n=>{n=clamp(n);return n*n*(3-2*n);};
  const mix=(a,b,t)=>a+(b-a)*t;
  // Only anatomical rotations: fixed forearm -> wrist -> MCP -> PIP -> distal link.
  // The robot has two visible bearings; the terminal cap is not an invented third hinge.
  const hands={
    left:{wrist:[258,376],joints:[[621,312],[758,330]],tip:robotTip,limits:[.14,2,2.4],angles:[0,0,0],targets:[0,0,0]},
    right:{wrist:[1535,421],joints:[[1257,309],[1109,326],[1026,348]],tip:aiTip,limits:[-.12,-1.6,-1.8,-.8],angles:[0,0,0,0],targets:[0,0,0,0]}
  };
  // Each folded digit owns a narrow region around its visible links. Hidden roots
  // move less than exposed joints; the foreground thumb retains overlap ownership.
  const digit=(joints,tip,limits,core,feather=10)=>({joints,tip,limits,core,feather,angles:limits.map(()=>0),targets:limits.map(()=>0)});
  hands.left.digits={
    thumb:digit([[484,455],[568,518]],[616,575],[.85,1.25],24),
    middle:digit([[643,357],[699,438],[713,537]],[681,614],[.55,1.15,1.35],20),
    ring:digit([[594,379],[637,475],[633,553]],[572,618],[.45,.95,1.15],16),
    pinky:digit([[535,543]],[511,589],[.6],12,8)
  };
  hands.right.digits={
    thumb:digit([[1435,423],[1371,491],[1304,545]],[1270,577],[-.55,-1,-1.15],23),
    middle:digit([[1240,375],[1148,438],[1107,513]],[1118,577],[-.55,-1.1,-1.25],18),
    ring:digit([[1280,395],[1204,466],[1207,543]],[1227,601],[.45,.95,1.15],17),
    pinky:digit([[1290,410],[1258,440],[1283,465]],[1308,450],[-.3,-.4,-.45],8,6)
  };
  // Increase finger curls by 50%; keep wrist rotation anchored at its original range.
  for(const hand of Object.values(hands)){
    hand.limits=hand.limits.map((limit,index)=>index===0?limit:limit*1.5);
    for(const finger of Object.values(hand.digits))finger.limits=finger.limits.map(limit=>limit*1.5);
  }
  const articulations=Object.values(hands).flatMap(hand=>[hand,...Object.values(hand.digits)]);
  const aiGuides=ceramic?[
    [[1768,237],[1695,274],[1637,319],[1572,357],[1490,346],[1414,313],[1332,279],[1255,300],[1142,304],[1041,331],aiTip],
    [[1768,265],[1690,300],[1619,338],[1553,368],[1470,354],[1394,329],[1320,301],[1230,321],[1128,327],[1026,345],aiTip],
    [[1768,295],[1686,326],[1606,361],[1519,385],[1433,364],[1351,338],[1281,331],[1190,345],[1092,353],[1000,362],aiTip]
  ]:[
    [[1768,211],[1695,229],[1632,284],[1565,341],[1461,330],[1356,307],[1263,285],[1198,304],[1107,307],[1025,330],aiTip],
    [[1768,237],[1698,263],[1624,307],[1560,350],[1455,345],[1362,321],[1270,309],[1187,326],[1093,328],[1014,344],aiTip],
    [[1768,265],[1685,300],[1606,341],[1522,367],[1425,353],[1340,330],[1250,338],[1175,348],[1088,349],[1008,362],aiTip]
  ];
  function route(control){
    const points=[];
    for(let i=0;i<control.length-1;i++){
      const a=control[Math.max(0,i-1)],b=control[i],c=control[i+1],d=control[Math.min(control.length-1,i+2)];
      for(let s=0;s<16;s++){const t=s/16,t2=t*t,t3=t2*t;points.push([0,1].map(k=>.5*(2*b[k]+(-a[k]+c[k])*t+(2*a[k]-5*b[k]+4*c[k]-d[k])*t2+(-a[k]+3*b[k]-3*c[k]+d[k])*t3)));}
    }
    points.push(control[control.length-1]);let length=0;const distances=[0];
    for(let i=1;i<points.length;i++){length+=Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]);distances.push(length);}
    return{points,distances,length};
  }
  const aiRoutes=aiGuides.map(route);
  const robotRoute=route([robotTip,[818,345],[758,330],[687,307],[621,312],[550,291],[475,291],[369,311],[284,315],[200,279],[113,247],[0,213]]);
  function sample(path,distance){
    const target=clamp(distance,0,path.length);let lo=0,hi=path.points.length-1;
    while(lo+1<hi){const mid=(lo+hi)>>1;if(path.distances[mid]<target)lo=mid;else hi=mid;}
    const t=(target-path.distances[lo])/Math.max(.001,path.distances[hi]-path.distances[lo]);
    return[0,1].map(k=>mix(path.points[lo][k],path.points[hi][k],t));
  }
  function segmentDistance(p,a,b){
    const dx=b[0]-a[0],dy=b[1]-a[1],t=clamp(((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy));
    return Math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy);
  }
  function fingerSkin(p,finger){
    const chain=[...finger.joints,finger.tip],distances=[0];let nearest=Infinity,along=0,length=0;
    for(let i=0;i<chain.length-1;i++){
      const a=chain[i],b=chain[i+1],dx=b[0]-a[0],dy=b[1]-a[1],size=Math.hypot(dx,dy);
      const t=clamp(((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(size*size));
      const distance=Math.hypot(p[0]-a[0]-t*dx,p[1]-a[1]-t*dy);
      if(distance<nearest){nearest=distance;along=length+t*size;}
      length+=size;distances.push(length);
    }
    const influence=1-smooth((nearest-finger.core)/finger.feather);
    return{distance:nearest,influence,weights:finger.joints.map((_,i)=>influence*smooth((along-distances[i]+9)/18))};
  }
  function weights(p,name){
    const hand=hands[name],left=name==='left',chain=[...hand.joints,hand.tip];let distance=Infinity;
    for(let i=0;i<chain.length-1;i++)distance=Math.min(distance,segmentDistance(p,chain[i],chain[i+1]));
    const index=1-smooth((distance-(left?28:26))/16);
    const inside=smooth((p[1]-185)/55)*(1-smooth((p[1]-650)/65));
    const skin=[smooth((left?p[0]-238:1560-p[0])/55)*inside,...hand.joints.map(j=>index*smooth(((left?p[0]-j[0]:j[0]-p[0])+12)/24))];
    // A mesh point belongs to one digit. Do not add multiple neighboring rotations
    // to the same glass strand or metal link where the folded fingers overlap.
    if(index<.4){
      const candidates=Object.entries(hand.digits).map(([finger,rig])=>({finger,rig,...fingerSkin(p,rig)}));
      const thumb=candidates.find(candidate=>candidate.finger==='thumb');let best=null;
      for(const candidate of candidates){
        if(!candidate.influence)continue;
        if(left&&candidate.finger!=='thumb'&&thumb.distance<hand.digits.thumb.core)continue;
        const score=candidate.distance/candidate.rig.core-(candidate.finger==='thumb'?.08:0);
        if(!best||score<best.score)best={...candidate,score};
      }
      if(best){skin.finger=best.finger;skin.fingerWeights=best.weights;for(let i=1;i<skin.length;i++)skin[i]=0;}
    }
    return skin;
  }
  function rotate(p,pivot,angle){
    if(!angle)return p;const c=Math.cos(angle),s=Math.sin(angle),x=p[0]-pivot[0],y=p[1]-pivot[1];
    return[pivot[0]+x*c-y*s,pivot[1]+x*s+y*c];
  }
  // Distal pivots inherit all upstream rotations. Full link weights preserve rigid
  // lengths, with small blend regions only at a joint seam or index silhouette.
  function chainPose(hand,joints,angles){
    const pivots=joints.map(j=>rotate(j,hand.wrist,hand.angles[0]*DEG));
    for(let i=0;i<pivots.length;i++)for(let j=i+1;j<pivots.length;j++)pivots[j]=rotate(pivots[j],pivots[i],angles[i]*DEG);
    return pivots;
  }
  function updatePoses(){
    for(const hand of Object.values(hands)){
      hand.posedJoints=chainPose(hand,hand.joints,hand.angles.slice(1));
      for(const finger of Object.values(hand.digits))finger.posedJoints=chainPose(hand,finger.joints,finger.angles);
    }
  }
  function deform(p,name,skin=weights(p,name)){
    const hand=hands[name];let point=rotate(p,hand.wrist,hand.angles[0]*DEG*skin[0]);
    if(skin.finger){
      const finger=hand.digits[skin.finger];
      for(let i=0;i<finger.joints.length;i++)point=rotate(point,finger.posedJoints[i],finger.angles[i]*DEG*skin.fingerWeights[i]);
    }else for(let i=0;i<hand.joints.length;i++)point=rotate(point,hand.posedJoints[i],hand.angles[i+1]*DEG*skin[i+1]);
    return point;
  }
  const mesh=[],indices=[],columns=148,rows=74;
  for(let y=0;y<=rows;y++)for(let x=0;x<=columns;x++){
    const point=[x/columns*W,y/rows*H],name=point[0]<900?'left':'right';mesh.push({point,name,skin:weights(point,name)});
  }
  for(let y=0;y<rows;y++)for(let x=0;x<columns;x++){
    const a=y*(columns+1)+x,b=a+1,c=a+columns+1,d=c+1;indices.push(a,c,b,b,c,d);
  }
  const vertices=new Float32Array(mesh.length*4);
  const lightCanvas=document.createElement('canvas'),bridgeCanvas=document.createElement('canvas');
  const workCanvas=document.createElement('canvas'),cleanCanvas=document.createElement('canvas');
  const light=lightCanvas.getContext('2d'),bridge=bridgeCanvas.getContext('2d'),work=workCanvas.getContext('2d');
  let aiMask,robotMask,renderer,ready=false,destroyed=false,frame=0,last=0;
  let visible=!document.hidden,onScreen=true,pausedAt=null,hovered=false,pulseCount=0,completedPulseCount=0;
  const pulses=[]; // Clicks append; they never replace another pulse or reset its birth.
  Object.assign(stage.dataset,{ready:'false',pulseCount:'0',activePulseCount:'0',completedPulseCount:'0',pulseOrigin:'ai-wrist',pulseDestination:'robot-wrist',pulseActive:'false',pulseStates:'[]',pulseProgress:'0',hoverState:'rest',leftHandAngle:'0',rightHandAngle:'0'});
  canvas.style.pointerEvents='none';art.style.transform='none';
  function prepareArt(){
    cleanCanvas.width=W;cleanCanvas.height=H;const clean=cleanCanvas.getContext('2d',{willReadFrequently:true});clean.drawImage(image,0,0,W,H);
    // Erase only the static bridge on an in-memory moving texture. Keep the PNG intact.
    try{
      const gapX=robotTip[0]+10,gapWidth=aiTip[0]-gapX-4,strip=clean.getImageData(gapX,robotTip[1]-8,gapWidth,17);
      // Interpolate alpha as well as color: an extracted transparent bridge must
      // become transparent here, while an opaque source keeps its original backdrop.
      for(let y=1;y<16;y++)for(let x=0;x<gapWidth;x++)for(let c=0;c<4;c++)strip.data[(y*gapWidth+x)*4+c]=mix(strip.data[x*4+c],strip.data[(16*gapWidth+x)*4+c],y/16);
      clean.putImageData(strip,gapX,robotTip[1]-8);
    }catch(_){clean.clearRect(robotTip[0]+10,robotTip[1]-2,aiTip[0]-robotTip[0]-14,4);}
    const buffer=document.createElement('canvas');buffer.width=887;buffer.height=444;
    const ctx=buffer.getContext('2d',{willReadFrequently:true});ctx.drawImage(image,0,0,887,444);
    try{
      const original=ctx.getImageData(0,0,887,444);
      for(const name of ['ai','robot']){
        const mask=document.createElement('canvas');mask.width=887;mask.height=444;const mc=mask.getContext('2d'),pixels=mc.createImageData(887,444);
        for(let y=0;y<444;y++)for(let x=0;x<887;x++){
          const i=(y*887+x)*4,r=original.data[i],g=original.data[i+1],b=original.data[i+2];
          const value=name==='ai'?clamp((Math.max(r,g,b)-32)/90)*clamp((b-r+4)/21)*(x*2>=aiTip[0]-5?1:0):clamp((Math.max(r,g,b)-22)/70)*(x*2<=robotTip[0]+8?1:0);
          pixels.data[i]=pixels.data[i+1]=pixels.data[i+2]=255;pixels.data[i+3]=Math.round(Math.pow(value,.65)*original.data[i+3]);
        }
        mc.putImageData(pixels,0,0);if(name==='ai')aiMask=mask;else robotMask=mask;
      }
    }catch(_){aiMask=robotMask=null;}
  }
  function setupRenderer(){
    // Filter premultiplied texels so transparent edge pixels cannot create a dark
    // fringe. The WebGL canvas itself expects straight-alpha shader output.
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL,true);
    const shader=(type,source)=>{const item=gl.createShader(type);gl.shaderSource(item,source);gl.compileShader(item);if(!gl.getShaderParameter(item,gl.COMPILE_STATUS))throw new Error(gl.getShaderInfoLog(item));return item;};
    const program=gl.createProgram();
    gl.attachShader(program,shader(gl.VERTEX_SHADER,'attribute vec2 position; attribute vec2 uv; varying vec2 sourceUV; varying vec2 screenUV; void main(){sourceUV=uv;screenUV=position;gl_Position=vec4(position.x*2.0-1.0,1.0-position.y*2.0,0.0,1.0);}'));
    gl.attachShader(program,shader(gl.FRAGMENT_SHADER,`
      precision highp float;
      varying vec2 sourceUV;
      varying vec2 screenUV;
      uniform sampler2D art;
      uniform sampler2D lights;
      uniform sampler2D connection;
      void main(){
        vec4 base=texture2D(art,sourceUV);
        vec4 pulse=texture2D(lights,sourceUV);
        vec4 line=texture2D(connection,screenUV);
        // Pulse light brightens the hand without turning empty pixels opaque.
        vec3 lit=min(vec3(base.a),base.rgb+pulse.rgb*base.a);
        // The free-space bridge is a separate premultiplied source-over layer.
        float alpha=line.a+base.a*(1.0-line.a);
        vec3 color=line.rgb+lit*(1.0-line.a);
        gl_FragColor=vec4(alpha>0.0?color/alpha:vec3(0.0),alpha);
      }
    `));
    gl.linkProgram(program);if(!gl.getProgramParameter(program,gl.LINK_STATUS))throw new Error(gl.getProgramInfoLog(program));gl.useProgram(program);
    const vertexBuffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,vertexBuffer);
    const position=gl.getAttribLocation(program,'position'),uv=gl.getAttribLocation(program,'uv');gl.enableVertexAttribArray(position);gl.enableVertexAttribArray(uv);
    gl.vertexAttribPointer(position,2,gl.FLOAT,false,16,0);gl.vertexAttribPointer(uv,2,gl.FLOAT,false,16,8);
    const indexBuffer=gl.createBuffer();gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,indexBuffer);gl.bufferData(gl.ELEMENT_ARRAY_BUFFER,new Uint16Array(indices),gl.STATIC_DRAW);
    function texture(source){
      const result=gl.createTexture();gl.bindTexture(gl.TEXTURE_2D,result);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_S,gl.CLAMP_TO_EDGE);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_WRAP_T,gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.LINEAR);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.LINEAR);
      gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,source);return result;
    }
    const original=texture(image),clean=texture(cleanCanvas),pulse=texture(lightCanvas),line=texture(bridgeCanvas);
    gl.uniform1i(gl.getUniformLocation(program,'art'),0);gl.uniform1i(gl.getUniformLocation(program,'lights'),1);gl.uniform1i(gl.getUniformLocation(program,'connection'),2);
    return{program,vertexBuffer,indexBuffer,original,clean,pulse,line};
  }
  const PULSE_GAIN=1.5;
  function glow(ctx,p,strength,radius=24){
    strength*=PULSE_GAIN;
    if(strength<=0)return;const g=ctx.createRadialGradient(p[0],p[1],0,p[0],p[1],radius);
    g.addColorStop(0,`rgba(224,255,255,${clamp(strength)})`);g.addColorStop(.14,`rgba(161,245,255,${clamp(strength*.86)})`);g.addColorStop(.45,`rgba(79,211,248,${clamp(strength*.29)})`);g.addColorStop(1,'rgba(66,198,240,0)');
    ctx.fillStyle=g;ctx.fillRect(p[0]-radius,p[1]-radius,radius*2,radius*2);
  }
  function stroke(ctx,a,b,strength,width=1.6){ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.lineWidth=width;ctx.lineCap='round';ctx.strokeStyle=`rgba(131,237,255,${clamp(strength)})`;ctx.stroke();}
  function routeLight(ctx,path,progress,strength,radius=25){
    const distance=clamp(progress)*path.length;
    for(let tail=0;tail<10;tail++){const end=distance-tail*8;if(end<0)continue;stroke(ctx,sample(path,end-8),sample(path,end),strength*(1-tail/10)*.62*PULSE_GAIN,2.1);}
    glow(ctx,sample(path,distance),strength,radius);glow(ctx,sample(path,Math.max(0,distance-20)),strength*.3,radius*.6);
  }
  function clear(ctx,target){ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,target.width,target.height);ctx.setTransform(target.width/W,0,0,target.height/H,0,0);}
  function compositeMask(mask){
    work.setTransform(1,0,0,1,0,0);if(mask){work.globalCompositeOperation='destination-in';work.drawImage(mask,0,0,workCanvas.width,workCanvas.height);work.globalCompositeOperation='source-over';}
    light.setTransform(1,0,0,1,0,0);light.drawImage(workCanvas,0,0);
  }
  function phaseFor(pulse,now){
    const age=Math.max(0,(now-pulse.born)/1000);
    return{id:pulse.id,age:+age.toFixed(3),phase:pulse.reduced?'highlight':age<1.7?'ai-hand':age<2?'bridge':age<4?'robot-hand':'robot-wrist',progress:+clamp(age/(pulse.duration/1000)).toFixed(3)};
  }
  function lighting(now,moving){
    clear(light,lightCanvas);clear(bridge,bridgeCanvas);clear(work,workCanvas);
    const rightTip=deform(aiTip,'right'),leftTip=deform(robotTip,'left');if(moving)stroke(bridge,rightTip,leftTip,.4,1.05);
    for(const pulse of pulses){
      const age=(now-pulse.born)/1000;
      if(pulse.reduced){for(const path of aiRoutes)for(let i=1;i<path.points.length;i++)stroke(work,path.points[i-1],path.points[i],.15,2.2);}
      else aiRoutes.forEach((path,index)=>{const local=age-index*.024;if(local<0||local>1.85)return;routeLight(work,path,local/1.7,clamp(local/.09)*clamp((1.85-local)/.15)*(index?.85:1),26);});
    }
    compositeMask(aiMask);clear(work,workCanvas);
    for(const pulse of pulses){
      const age=(now-pulse.born)/1000;
      if(pulse.reduced){
        for(let i=1;i<robotRoute.points.length;i++)stroke(work,robotRoute.points[i-1],robotRoute.points[i],.23,2.8);
        glow(work,[5,213],.35,25);stroke(bridge,rightTip,leftTip,.25,1.4);
      }else{
        if(age>=2){const fade=clamp((4.18-age)/.18);routeLight(work,robotRoute,(age-2)/2,.98*fade,28);if(age>3.9){glow(work,[8,213],.8*clamp((age-3.9)/.1)*fade,35);glow(work,[44,224],.38*fade,20);}}
        if(age>=1.65&&age<=2.2){const t=clamp((age-1.7)/.3),head=[mix(rightTip[0],leftTip[0],t),mix(rightTip[1],leftTip[1],t)],fade=clamp((age-1.65)/.05)*clamp((2.2-age)/.2);stroke(bridge,rightTip,head,.43*fade*PULSE_GAIN,1.35);glow(bridge,head,.95*fade,19);}
      }
    }
    compositeMask(robotMask);
    stage.dataset.transformedTips=JSON.stringify({left:leftTip.map(v=>+v.toFixed(2)),right:rightTip.map(v=>+v.toFixed(2))});
  }
  function draw(now){
    if(!renderer||!canvas.width||!canvas.height)return;
    updatePoses();
    const moving=articulations.some(rig=>rig.angles.some(a=>Math.abs(a)>.0001));lighting(now,moving);
    for(let i=0;i<mesh.length;i++){const node=mesh[i],p=moving?deform(node.point,node.name,node.skin):node.point;vertices[i*4]=p[0]/W;vertices[i*4+1]=p[1]/H;vertices[i*4+2]=node.point[0]/W;vertices[i*4+3]=node.point[1]/H;}
    gl.viewport(0,0,canvas.width,canvas.height);gl.clearColor(0,0,0,0);gl.clear(gl.COLOR_BUFFER_BIT);gl.useProgram(renderer.program);gl.bindBuffer(gl.ARRAY_BUFFER,renderer.vertexBuffer);gl.bufferData(gl.ARRAY_BUFFER,vertices,gl.DYNAMIC_DRAW);gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER,renderer.indexBuffer);
    gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,moving?renderer.clean:renderer.original);
    gl.activeTexture(gl.TEXTURE1);gl.bindTexture(gl.TEXTURE_2D,renderer.pulse);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,lightCanvas);
    gl.activeTexture(gl.TEXTURE2);gl.bindTexture(gl.TEXTURE_2D,renderer.line);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,gl.RGBA,gl.UNSIGNED_BYTE,bridgeCanvas);
    gl.drawElements(gl.TRIANGLES,indices.length,gl.UNSIGNED_SHORT,0);
    stage.dataset.leftHandAngle=hands.left.angles[0].toFixed(3);stage.dataset.rightHandAngle=hands.right.angles[0].toFixed(3);
    stage.dataset.joints=JSON.stringify(Object.fromEntries(Object.entries(hands).map(([name,hand])=>[name,{
      wrist:{pivot:hand.wrist,angle:+hand.angles[0].toFixed(3)},
      index:hand.joints.map((pivot,i)=>({pivot,angle:+hand.angles[i+1].toFixed(3)})),
      ...Object.fromEntries(Object.entries(hand.digits).map(([finger,rig])=>[finger,rig.joints.map((pivot,i)=>({pivot,angle:+rig.angles[i].toFixed(3)}))]))
    }])));
    stage.dataset.movingSegments=JSON.stringify(Object.entries(hands).flatMap(([name,hand])=>[
      ...hand.angles.map((a,i)=>Math.abs(a)>.01?`${name}-${i===0?'wrist':`index-${i}`}`:null),
      ...Object.entries(hand.digits).flatMap(([finger,rig])=>rig.angles.map((a,i)=>Math.abs(a)>.01?`${name}-${finger}-${i+1}`:null))
    ].filter(Boolean)));
  }
  function telemetry(now){
    stage.dataset.pulseCount=String(pulseCount);stage.dataset.activePulseCount=String(pulses.length);stage.dataset.completedPulseCount=String(completedPulseCount);stage.dataset.pulseActive=String(pulses.length>0);
    const states=pulses.map(p=>phaseFor(p,now));stage.dataset.pulseStates=JSON.stringify(states);stage.dataset.pulseProgress=states.length?String(states[states.length-1].progress):'0';
  }
  function tick(now){
    frame=0;if(!ready||destroyed||!visible||!onScreen)return;const dt=Math.min((now-last)/1000||.016,.05);last=now;let changing=false;
    for(const rig of articulations)for(let i=0;i<rig.angles.length;i++){const target=reduced.matches?0:rig.targets[i],difference=target-rig.angles[i];rig.angles[i]+=difference*(1-Math.exp(-dt*10));if(Math.abs(difference)<.001)rig.angles[i]=target;else changing=true;}
    for(let i=pulses.length-1;i>=0;i--)if(now-pulses[i].born>=pulses[i].duration){pulses.splice(i,1);completedPulseCount++;}
    telemetry(now);draw(now);if(changing||pulses.length)frame=requestAnimationFrame(tick);
  }
  function wake(){if(!frame&&ready&&!destroyed&&visible&&onScreen){last=performance.now();frame=requestAnimationFrame(tick);}}
  function playbackVisibility(){
    const now=performance.now();if(!visible||!onScreen){if(pausedAt===null)pausedAt=now;cancelAnimationFrame(frame);frame=0;}
    else{if(pausedAt!==null){const delta=now-pausedAt;for(const pulse of pulses)pulse.born+=delta;pausedAt=null;}wake();}
  }
  function resize(){
    const width=art.clientWidth,height=art.clientHeight;if(!width||!height)return;const dpr=Math.min(devicePixelRatio||1,2);canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
    for(const target of [lightCanvas,bridgeCanvas,workCanvas]){target.width=Math.min(canvas.width,W);target.height=Math.round(target.width*H/W);}if(ready)draw(pausedAt??performance.now());
  }
  function pointAt(event){
    if(reduced.matches||event.pointerType==='touch')return;const b=art.getBoundingClientRect(),p=[(event.clientX-b.left)/b.width*W,(event.clientY-b.top)/b.height*H];hovered=true;stage.dataset.hoverState='articulating';
    for(const [name,hand] of Object.entries(hands)){
      const pivots=[hand.wrist,...hand.joints];
      for(let i=0;i<pivots.length;i++){const pivot=pivots[i],distance=Math.hypot((p[0]-pivot[0])/.95,(p[1]-pivot[1])*1.3),proximity=.4+.6*(1-clamp(distance/950)),curl=.8+.2*clamp((p[1]-pivot[1])/220,-1,1);hand.targets[i]=hand.limits[i]*proximity*curl;}
      for(const finger of Object.values(hand.digits))for(let i=0;i<finger.joints.length;i++){
        const pivot=finger.joints[i],distance=Math.hypot(p[0]-pivot[0],(p[1]-pivot[1])*1.2);
        const proximity=.48+.52*(1-clamp(distance/900)),curl=.85+.15*clamp((p[1]-pivot[1])/230,-1,1);
        finger.targets[i]=finger.limits[i]*proximity*curl;
      }
      stage.dataset[`${name}HoverJoint`]=String(pivots.reduce((nearest,pivot,i)=>Math.hypot(p[0]-pivot[0],p[1]-pivot[1])<Math.hypot(p[0]-pivots[nearest][0],p[1]-pivots[nearest][1])?i:nearest,0));
    }
    wake();
  }
  function rest(){hovered=false;stage.dataset.hoverState='rest';for(const rig of articulations)rig.targets.fill(0);wake();}
  stage.addEventListener('pointerenter',pointAt);stage.addEventListener('pointermove',pointAt);stage.addEventListener('pointerleave',rest);stage.addEventListener('pointercancel',rest);stage.addEventListener('blur',rest);
  stage.addEventListener('click',()=>{
    if(!ready)return;const now=pausedAt??performance.now();pulses.push({id:++pulseCount,born:now,duration:reduced.matches?480:4180,reduced:reduced.matches});telemetry(now);
    if(status)status.textContent=reduced.matches?'Both hands and their connection highlighted.':`Signal ${pulseCount} travels from the right AI wrist through both hands to the left robot wrist.`;wake();
  });
  reduced.addEventListener('change',()=>{
    const now=performance.now();if(reduced.matches){rest();for(const rig of articulations)rig.angles.fill(0);for(const pulse of pulses){pulse.reduced=true;pulse.duration=Math.min(pulse.duration,now-pulse.born+480);}}draw(now);wake();
  });
  const resizeObserver=new ResizeObserver(resize);resizeObserver.observe(art);
  const intersectionObserver=new IntersectionObserver(entries=>{onScreen=entries[0].isIntersecting;playbackVisibility();});intersectionObserver.observe(stage);
  document.addEventListener('visibilitychange',()=>{visible=!document.hidden;playbackVisibility();});
  window.addEventListener('pagehide',event=>{cancelAnimationFrame(frame);frame=0;if(!event.persisted){destroyed=true;resizeObserver.disconnect();intersectionObserver.disconnect();}else{visible=false;playbackVisibility();}});
  window.addEventListener('pageshow',()=>{visible=!document.hidden;playbackVisibility();});
  canvas.addEventListener('webglcontextlost',event=>{event.preventDefault();ready=false;cancelAnimationFrame(frame);frame=0;image.style.opacity='1';canvas.style.opacity='0';stage.dataset.ready='false';});
  canvas.addEventListener('webglcontextrestored',()=>{renderer=null;initialize();});
  function initialize(){
    if(ready||!image.naturalWidth)return;
    try{prepareArt();resize();renderer=setupRenderer();draw(performance.now());if(gl.getError()!==gl.NO_ERROR)throw new Error('Hand renderer failed to initialize');canvas.style.maskImage=getComputedStyle(image).maskImage;canvas.style.webkitMaskImage=getComputedStyle(image).webkitMaskImage;image.style.opacity='0';canvas.style.opacity='1';ready=true;stage.dataset.ready='true';wake();}
    catch(error){image.style.opacity='1';canvas.style.opacity='0';stage.dataset.rendererError=String(error.message||error);}
  }
  if(image.complete&&image.naturalWidth)initialize();else image.addEventListener('load',initialize,{once:true});
})();
