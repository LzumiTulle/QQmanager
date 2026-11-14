"""Generate original geometric Q icons; no external or licensed image assets."""
import math,struct,zlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def png(size,tray=False):
 rows=[]
 for y in range(size):
  row=bytearray([0])
  for x in range(size):
   u=(x+.5)/size;v=(y+.5)/size
   radius=math.hypot(u-.48,v-.47)
   ring=.20<radius<.30
   tail=.50<u<.79 and abs(v-u-.04)<.055 and v>.53
   if tray:color=(20,20,20,255) if ring or tail else (0,0,0,0)
   else:
    corner=math.hypot(max(.17-u,0,u-.83),max(.17-v,0,v-.83))
    color=(255,255,255,255) if ring or tail else ((60,91,220,255) if corner<.17 else (0,0,0,0))
   row.extend(color)
  rows.append(bytes(row))
 def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
 return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',size,size,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(rows),9))+chunk(b'IEND',b'')
(ROOT/'desktop/tray.png').write_bytes(png(32,True))
(ROOT/'build-resources/icon.png').write_bytes(png(512))
pieces=[]
for code,size in [(b'icp4',16),(b'icp5',32),(b'icp6',64),(b'ic07',128),(b'ic08',256),(b'ic09',512),(b'ic10',1024)]:
 data=png(size);pieces.append(code+struct.pack('!I',len(data)+8)+data)
data=b''.join(pieces);(ROOT/'build-resources/icon.icns').write_bytes(b'icns'+struct.pack('!I',len(data)+8)+data)
data=png(256);(ROOT/'build-resources/icon.ico').write_bytes(struct.pack('<HHH',0,1,1)+struct.pack('<BBBBHHII',0,0,0,0,1,32,len(data),22)+data)
