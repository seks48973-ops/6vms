#!/usr/bin/env python3
# by @wp1d, @virelesss
# ts a file that came from discord.gg/tbjBPTRnph
# this script uses roblox_stubs.lua to emulate the roblox env
# keyforge obfuscation breakdown guide, not a full deobfuscator!!!!
a='setk'
Z='call'
Y='ret'
X='not'
W='cat'
V='table'
T='a'
S='t'
R='loadk'
Q=open
P=str
O=min
J='getk'
N='i'
M='g'
L='d'
K=''
I=False
H='h'
E=print
D=len
import sys as A,os as G,subprocess as e,tempfile as r,re as C
s='/home/elliot/lune' # change this to your lune path. sorry for hardcoding, im lazy
b={48558:R,96123:'gr',102900:'up',113500:J,121975:R,136538:V,142584:'nop',147644:W,197820:X,249648:'setr',261548:'jf',276303:'li',365325:'close',375219:J,467318:Y,477017:'vg',502445:'eq',508914:'fn',543194:'jm',547093:'le',547152:J,572793:'bl',582463:Z,597650:'ln',638485:'jt',692010:'mv',862939:a}
U='-- KeyForge Obfuscator (ForgeVM) [https://keyforge.win]'
def t(j):
	'Extract the important function names from obfuscated code';B={};j=j.replace(U,K,1);A=C.search('return\\s+\\w+\\s*\\((\\w+)\\[1\\]',j[-500:])
	if A:B[S]=A.group(1)
	A=C.search('local (\\w+)=0\\s+local (\\w+)=1\\s+local (\\w+)=2166136261\\s+local (\\w+)=0',j)
	if A:B[T]=A.group(1);B['b']=A.group(2);B['c']=A.group(3);B[L]=A.group(4)
	A=C.search('local function (\\w+)\\(\\)local \\w+=tonumber\\((\\w+)\\)',j)
	if A:B['e']=A.group(1)
	A=C.search('local function (\\w+)\\((\\w+),(\\w+)\\)local \\w+,\\w+=\\d+,\\d+ for \\w+,\\w+ in pairs',j)
	if A:B[H]=A.group(1)
	def G(n):
		A=j.find('local function',n+10);C=O(n+500,D(j))if A<0 else A;B=j.rfind('end ',n,C)
		if B>0:return j[n:B+3]
		return j[n:O(n+500,D(j))]
	R='local function (\\w+)\\((\\w+),(\\w+),(\\w+)\\)';V=B.get(H,'x');X=B.get(T,'n')
	for A in C.finditer(R,j):
		E=A.group(1)
		if D(E)<=10:
			F=G(A.start())
			if'if not 'in F and V in F and X in F:B[M]=E;break
	Y=B.get(L,'n')
	for A in C.finditer(R,j):
		E=A.group(1)
		if D(E)>10 or E==B.get(M):continue
		F=G(A.start())
		if V in F and'~='+Y in F:B[H]=E;break
	J=B.get('e',K)
	for A in C.finditer('local function (\\w+)\\((\\w+),(\\w+)\\)',j):
		E=A.group(1)
		if D(E)>10 or E in(B.get(H),):continue
		F=G(A.start());W=J in F if J else I;P=any(A in F for A in['nM2(','pM2(','orD(','oYD','goP(','yPk(','xPk(','zPk('])
		if not P:P=W and F.count('(')>5
		if W and P:B[N]=E;break
	if N not in B:
		for A in C.finditer('(?:local function |)(\\w+)\\((\\w+),(\\w+)\\)',j):
			if J in G(A.start()):B[N]=A.group(1);break
	for A in C.finditer('(\\w+)=function\\((\\w+)\\)local \\w+=\\(\\((\\w+)',j):
		Q=j[A.start():O(A.start()+400,D(j))]
		if'or'in Q and'and'in Q and'*65536+'in Q:B['j']=A.group(1);B['k']=A.group(2);break
	return B
def u(ad):
	'Map opcode numbers to names';Q='block';I='+1]';ad=ad.replace(U,K,1);E=C.search('\\.vmId==(\\d+)\\s+then\\s+do\\s+local\\s+(\\w+)=(\\w+)\\.id',ad)
	if not E:return{}
	S=E.group(2);M=E.start();N={}
	for E in C.finditer(S+'==(\\d+)\\s+then\\s+do\\s+(.*?)end\\s+else\\s+error',ad[M:M+150000]):F=int(E.group(1));G=E.group(2);N[F]=G
	H={}
	for(F,G)in N.items():
		O=b.get(F)
		if O:H[F]=O;continue
		A=G.replace(' ',K)
		if'=function('in A:B='fn'
		elif'hPR='in A:B=Y
		elif'pcall('in A:B=Z
		elif'if not'in A and(L in A or Q in A):B='jf'
		elif'if'in A and'then'in A and(L in A or Q in A):B='jt'
		elif'then oldP='in G or'true'in A and'while'not in A:B='jm'
		elif'..'in A:B=W
		elif'=not 'in A:B=X
		elif'=#'in A:B='ln'
		elif'.v'in A and'and 'in A:B='up'
		elif'~=0)'in A:B='bl'
		elif']['in A and I in A:B=a
		elif']['in A:B='gr'
		elif I in A and'=('in A:B=R
		elif I in A:B=J
		elif'{}'in A:B=V
		elif'=='in A:B='eq'
		elif'<='in A:B='le'
		elif'.A='in A:B='vg'
		elif'table.insert'in A:B='mv'
		else:B='o'+P(D(H)+1)
		H[F]=B
	return H
def B(ap,aq=None):
	'Main deobfuscator - pray this works';q='          end\n';p='          if type(db)=="string" then\n';o='        if bl then\n';n='(ca,cb,bw)\n';m='  end\n';l='[bj]\n';k='  local bk=';j='for bj=1,4 do\n';d=True;c='        end\n';b='        print("r"..cp.."="..cg[cp])\n';a='    ';Z=',';Y='end\n';X='    end\n';W='      end\n';J='\n'
	with Q(ap)as F:C=F.read()
	E('[*] '+G.path.basename(ap));B=t(C)
	if not all(A in B for A in[S,M,H,N]):E('not found');return I
	v=u(C);f=B[S];R=B.get('j');w,x,y,z=B[T],B['b'],B['c'],B[L];A0=B.get('k',M);A1=B[M];A2=B[H];A3=B[N];A='local opn={'
	for(A4,A5)in v.items():A+='['+P(A4)+']="'+A5+'",'
	A+='}\n';A+='local function ud()\n';A+=j;A+=k+f+l;A+='  if bk and bk._k then\n';A+='    local bl=bk._k\n';A+='    local bm=bk._ai or 1\n';A+='    local bn=bk._ad or 1129270867\n';A+='    local bo,bp=pcall(rM7,bk.sk or 0,bn,bm*16+1)\n';A+='    local bq,br=pcall(rM7,bk.ss or 0,bn,bm*16+3)\n';A+='    for bs=1,#bl do\n';A+='      local bt=bl[bs]\n';A+='      if type(bt)=="string" and bo and bq and rbH then\n';A+='        local bu,bv=pcall(rbH,bt,bp,br)\n';A+='        if bu then bl[bs]=bv end\n';A+=W;A+=X;A+=m;A+=Y;A+=j;A+=k+f+l;A+='  if bk and type(bk.stream)=="string" and #bk.stream>0 then\n';A+='    local bw=bk.stream\n';A+='    local bx=bk.blockOffsets or {[1]=1}\n';A+='    local by,bz=bk.entryOffset or 1,bk.entryBlock or 1\n';A+='    local ca,cb=by,bx\n';A+='    local cc,cd,ce,cf='+w+Z+x+Z+y+Z+z+J;A+=a+A0+'=bk.vmId or 0\n';A+=a+A1+n
	if R:A+='    if type('+R+')=="function" then '+R+'(bz) end\n'
	A+='    local bl=bk._k or {}\n';A+='    local cg={}\n';A+='    local ch={}\n';A+='    function ci(cj)\n';A+='      if not bl then return nil end\n';A+='      local ck=bl[cj+1]\n';A+='      if ck==nil then return nil end\n';A+='      if type(ck)=="string" then\n';A+='        local cl=ck:gsub("\'","\\\\\'")\n';A+='        return "\'"..cl.."\'"\n';A+=W;A+='      return tostring(ck)\n';A+=X;A+='    local cm=0\n';A+='    while cm<50000 do\n';A+='      '+A2+n;A+='      local cn\n';A+='      cn,ca='+A3+'(bw,ca)\n';A+='      if not cn then break end\n';A+='      cm=cm+1\n';A+='      local co=opn[cn.id] or "?"\n';A+='      local cp,cq,cr=(cn.A or 0),(cn.B or 0),(cn.C or 0)\n';A+='      if co=="loadk" then\n';A+='        local cs=ci(cq)\n';A+='        if cs then cg[cp]=cs;print("r"..cp.."="..cs)end\n';A+='      elseif co=="table" then\n';A+='        cg[cp]={};ch[cp]={};print("r"..cp.."={}")\n';A+='      elseif co=="fn" then\n';A+='        cg[cp]="f"..cq;ch[cp]="f"..cq;print("r"..cp.."=f"..cq)\n';A+='      elseif co=="up" then\n';A+='        cg[cp]="up"..cq;ch[cp]="up"..cq;print("r"..cp.."=up"..cq)\n';A+='      elseif co=="bl" then\n';A+='        local ct=cq~=0 and "true" or "false"\n';A+='        cg[cp]=ct;print("r"..cp.."="..ct)\n';A+='      elseif co=="li" then\n';A+='        cg[cp]=cq;print("r"..cp.."="..cq)\n';A+='      elseif co=="ret" then\n';A+='        print("ret "..(type(cg[cq])=="table" and "table" or (cg[cq] or "r"..cq)))\n';A+='      elseif co=="jf" then\n';A+='        print("if not "..(type(cg[cp])=="table" and "table" or (cg[cp] or "r"..cp)).." then L"..cq)\n';A+='      elseif co=="jt" then\n';A+='        print("if "..(type(cg[cp])=="table" and "table" or (cg[cp] or "r"..cp)).." then L"..cq)\n';A+='      elseif co=="jm" then\n';A+='        print("goto L"..cp)\n';A+='      elseif co=="eq" then\n';A+='        print("if "..(type(cg[cq])=="table" and "table" or (cg[cq] or "r"..cq)).."=="..(type(cg[cr])=="table" and "table" or (cg[cr] or "r"..cr)).." then")\n';A+='      elseif co=="le" then\n';A+='        print("if "..(type(cg[cq])=="table" and "table" or (cg[cq] or "r"..cq)).."<="..(type(cg[cr])=="table" and "table" or (cg[cr] or "r"..cr)).." then")\n';A+='      elseif co=="cat" then\n';A+='        local cu=type(cg[cq])=="table" and "table" or (cg[cq] or "r"..cq)\n';A+='        local cv=type(cg[cr])=="table" and "table" or (cg[cr] or "r"..cr)\n';A+='        cg[cp]=cu..".."..cv\n';A+=b;A+='      elseif co=="not" then\n';A+='        cg[cp]="not "..(type(cg[cp])=="table" and "table" or (cg[cp] or "r"..cp))\n';A+=b;A+='      elseif co=="ln" then\n';A+='        print("#"..(type(cg[cq])=="table" and "table" or (cg[cq] or "r"..cq)))\n';A+='      elseif co=="call" then\n';A+='        local cw={}\n';A+='        for cx=1,cq-1 do\n';A+='          local cy=cp+cx\n';A+='          local cz=cg[cy] or ch[cy] or "r"..cy\n';A+='          cw[#cw+1]=type(cz)=="table" and "table" or cz\n';A+=c;A+='        local da=type(cg[cp])=="table" and "table" or (cg[cp] or "r"..cp)\n';A+='        print(da.."("..table.concat(cw,",")..")")\n';A+='        cg[cp]=nil\n';A+='      elseif co=="setr" then\n';A+='        print("r"..cp.."["..(type(cg[cq])=="table" and "table" or (cg[cq] or "r"..cq)).."]="..(type(cg[cr])=="table" and "table" or (cg[cr] or "r"..cr)))\n';A+='      elseif co=="gr" then\n';A+='        cg[cp]="r"..cq.."["..(type(cg[cr])=="table" and "table" or (cg[cr] or "r"..cr)).."]"\n';A+=b;A+='      elseif co=="mv" then\n';A+='        cg[cp]=cg[cq];ch[cp]=ch[cq]\n';A+='        print((type(ch[cp])=="table" and "table" or (ch[cp] or "r"..cp)).."="..(type(ch[cq])=="table" and "table" or (ch[cq] or "r"..cq)))\n';A+='      elseif co=="setk" then\n';A+=o;A+='          local db=bl[cq+1]\n';A+=p;A+='            print((type(ch[cp])=="table" and "table" or (ch[cp] or "r"..cp)).."."..db.."="..(type(cg[cr])=="table" and "table" or (cg[cr] or "r"..cr)))\n';A+=q;A+=c;A+='      elseif co=="getk" then\n';A+=o;A+='          local db=bl[cr+1]\n';A+=p;A+='            cg[cp]=(type(ch[cq])=="table" and "table" or (ch[cq] or "r"..cq)).."."..db\n';A+='            print("r"..cp.."="..cg[cp])\n';A+=q;A+=c;A+='      elseif co=="nop" or co=="vg" then\n';A+='        -- do nothing\n';A+='      else\n';A+='        print("-- "..co.." "..cp.." "..cq.." "..cr)\n';A+=W;A+=X;A+='    print("end")\n';A+=m;A+=Y;A+=Y;A+='ud()\n';U=C.rfind('return ');A6=C.rfind('end))(')
	if U<0 or A6<0:E('entry point not found');return I
	g=K
	if'game:GetService'in C or'Instance.new'in C:
		h=G.path.join(G.path.dirname(G.path.abspath(__file__)),'roblox_stubs.lua')
		if G.path.exists(h):
			with Q(h)as F:g=F.read()
			E('    + roblox stubs')
	A7=C[:U]+J+g+J+A+J+C[U:];O=r.NamedTemporaryFile(mode='w',suffix='.luau',delete=I);O.write(A7);O.close()
	try:
		V=e.run([s,'run',O.name],capture_output=d,text=d,timeout=60)
		if V.returncode!=0:E('lune error:',V.stderr[:200]);return I
		i=V.stdout.strip();E(a+P(D(i.split(J)))+' lines')
		if aq:
			with Q(aq,'w')as F:F.write('-- ts a file that came from discord.gg/tbjBPTRnph\n');F.write('-- keyforge deobfuscator by @wp1d, @virelesss\n');F.write(i+J)
			E('    saved: '+aq)
		return d
	except e.TimeoutExpired:E('timeout');return I
	finally:
		try:G.unlink(O.name)
		except:pass
if __name__=='__main__':
	if D(A.argv)<2:E('usage: {} <file> [output]'.format(A.argv[0]));A.exit(1)
	F=B(A.argv[1],A.argv[2]if D(A.argv)>2 else None);A.exit(0 if F else 1)
