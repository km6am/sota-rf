#!/usr/bin/env python3
"""Write tiny synthetic files in the exact raw FCC/SOTA formats, for testing
sota_rf_sources.py without downloading the real (large) datasets."""
import zipfile, os
os.makedirs("fixtures", exist_ok=True)

summit_csv = """SOTAData Version=,2026-06-22,,,,,,,,,,,,,,,
SummitCode,AssociationName,RegionName,SummitName,AltM,AltFt,GridRef1,GridRef2,Longitude,Latitude,Points,BonusPoints,ValidFrom,ValidTo,ActivationCount,ActivationDate,ActivationCall
W6/CC-035,W6 - California,Coastal Central,Mount Diablo,1173,3848,,,-121.9142,37.8816,4,0,01/01/2010,31/12/2099,250,01/06/2026,K6XYZ
W6/NS-001,W6 - California,North Sierra,Some Far Peak,2000,6561,,,-120.0000,38.5000,8,0,01/01/2010,31/12/2099,30,01/05/2026,K6ABC
W7W/KG-001,W7W - Washington,King,Other State Peak,1500,4921,,,-121.5000,47.5000,6,0,01/01/2010,31/12/2099,12,,
"""
open("fixtures/summitslist.csv","w").write(summit_csv)

def co(usi, reg, lat, lon):
    r=[""]*18; r[0]="CO"; r[3]=reg; r[4]=usi
    r[9]="N" if lat>=0 else "S"; r[10]=f"{abs(lat)*3600:.1f}"
    r[14]="W" if lon<0 else "E"; r[15]=f"{abs(lon)*3600:.1f}"; return "|".join(r)
def ra(usi, reg, htype, h_agl, h_amsl, built="20100101", dismantled=""):
    r=[""]*35; r[0]="RA"; r[3]=reg; r[4]=usi; r[12]=built; r[13]=dismantled
    r[25]="CA"; r[28]="40"; r[29]="1100"; r[30]=str(h_agl); r[31]=str(h_amsl); r[32]=htype
    return "|".join(r)
def en_asr(usi, name):
    r=[""]*30; r[0]="EN"; r[4]=usi; r[9]=name; return "|".join(r)

with zipfile.ZipFile("fixtures/r_tower.zip","w") as z:
    z.writestr("CO.dat","\n".join([co("1001","1234567",37.8820,-121.9145),
        co("1002","2222222",38.5005,-120.0010), co("1003","3333333",40.0,-100.0)])+"\n")
    z.writestr("RA.dat","\n".join([ra("1001","1234567","TOWER",120,1293),
        ra("1002","2222222","MAST",60,2060), ra("1003","3333333","TOWER",90,1090),
        ra("1004","4444444","TOWER",50,1050,dismantled="20200101")])+"\n")
    z.writestr("EN.dat","\n".join([en_asr("1001","KQED INC"),
        en_asr("1002","STATE OF CALIFORNIA"), en_asr("1003","FARM CO")])+"\n")

def hd(usi, call, status="A", svc="PW"):
    r=[""]*60; r[0]="HD"; r[1]=usi; r[4]=call; r[5]=status; r[6]=svc
    r[7]="01/01/2020"; r[8]="01/01/2030"; return "|".join(r)
def en_uls(usi, call, name):
    r=[""]*30; r[0]="EN"; r[1]=usi; r[4]=call; r[7]=name; return "|".join(r)
def lo(usi, call, lat, lon, name, tower_reg=""):
    r=[""]*51; r[0]="LO"; r[1]=usi; r[4]=call; r[6]="T"; r[8]="1"
    ld=int(abs(lat)); lm=int((abs(lat)-ld)*60); ls=(((abs(lat)-ld)*60)-lm)*60
    od=int(abs(lon)); om=int((abs(lon)-od)*60); os_=(((abs(lon)-od)*60)-om)*60
    r[19]=str(ld); r[20]=str(lm); r[21]=f"{ls:.1f}"; r[22]="N" if lat>=0 else "S"
    r[23]=str(od); r[24]=str(om); r[25]=f"{os_:.1f}"; r[26]="W" if lon<0 else "E"
    r[37]=tower_reg; r[42]=name; return "|".join(r)
def fr(usi, call, freq, p_out, p_erp, loc="1"):
    r=[""]*20; r[0]="FR"; r[1]=usi; r[4]=call; r[6]=loc
    r[10]=str(freq); r[15]=str(p_out); r[16]=str(p_erp); return "|".join(r)

with zipfile.ZipFile("fixtures/l_LMcomm.zip","w") as z:
    z.writestr("HD.dat","\n".join([hd("5001","WPAA123"), hd("5002","WQQQ999"),
        hd("5003","WCANCEL","C")])+"\n")
    z.writestr("EN.dat","\n".join([en_uls("5001","WPAA123","CONTRA COSTA COUNTY"),
        en_uls("5002","WQQQ999","DIABLO TWO WAY RADIO")])+"\n")
    z.writestr("LO.dat","\n".join([
        lo("5001","WPAA123",37.8835,-121.9120,"MT DIABLO PEAK",tower_reg="1234567"),
        lo("5002","WQQQ999",37.8700,-121.9300,"DIABLO LOWER SHOULDER"),
        lo("5003","WCANCEL",37.8810,-121.9150,"GHOST SITE")])+"\n")
    z.writestr("FR.dat","\n".join([fr("5001","WPAA123",155.745,110,250),
        fr("5001","WPAA123",154.205,110,250), fr("5002","WQQQ999",462.55,40,75)])+"\n")

print("wrote fixtures/:", os.listdir("fixtures"))
