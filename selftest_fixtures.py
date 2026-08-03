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
        hd("5003","WCANCEL","C"),
        hd("5004","WMWX000",svc="CF")])+"\n")            # microwave (common-carrier fixed)
    z.writestr("EN.dat","\n".join([en_uls("5001","WPAA123","CONTRA COSTA COUNTY"),
        en_uls("5002","WQQQ999","DIABLO TWO WAY RADIO"),
        en_uls("5004","WMWX000","DIABLO BACKHAUL LLC")])+"\n")
    z.writestr("LO.dat","\n".join([
        lo("5001","WPAA123",37.8835,-121.9120,"MT DIABLO PEAK",tower_reg="1234567"),
        lo("5002","WQQQ999",37.8700,-121.9300,"DIABLO LOWER SHOULDER"),
        lo("5003","WCANCEL",37.8810,-121.9150,"GHOST SITE"),
        lo("5004","WMWX000",37.8825,-121.9130,"DIABLO MW TERMINAL")])+"\n")
    # microwave FR carries the freq but NO power (blank power_output/power_erp),
    # exactly like the live l_micro.zip -- must parse to a valid freq + NaN power.
    z.writestr("FR.dat","\n".join([fr("5001","WPAA123",155.745,110,250),
        fr("5001","WPAA123",154.205,110,250), fr("5002","WQQQ999",462.55,40,75),
        fr("5004","WMWX000",6034.15,"","")])+"\n")

# --------------------------------------------------------------------------- #
# Broadcast FM/TV/AM (FCC CDBS media files). Column counts match the live files
# (facility 32, fm_eng 73, tv_eng 75, am_eng 17, am_ant 41).
# --------------------------------------------------------------------------- #
def _dms(r, lat, lon, i_lat, i_lon):
    ld = int(abs(lat)); lm = int((abs(lat)-ld)*60); ls = (((abs(lat)-ld)*60)-lm)*60
    od = int(abs(lon)); om = int((abs(lon)-od)*60); os_ = (((abs(lon)-od)*60)-om)*60
    r[i_lat]=str(ld); r[i_lat+1]="N" if lat>=0 else "S"; r[i_lat+2]=str(lm); r[i_lat+3]=f"{ls:.1f}"
    r[i_lon]=str(od); r[i_lon+1]="W" if lon<0 else "E"; r[i_lon+2]=str(om); r[i_lon+3]=f"{os_:.1f}"

def facility(fid, call, service, status, freq, channel, city="DIABLO CITY", state="CA"):
    r=[""]*32; r[0]=city; r[1]=state; r[5]=call; r[6]=str(channel); r[9]=str(freq)
    r[10]=service; r[14]=str(fid); r[16]=status; return "|".join(r)
def fm_eng(fid, erp_h, lat, lon, asrn="", rec="C", cls="B", channel="", rcamsl="400"):
    r=[""]*73; r[19]=rec; r[20]=str(fid); r[9]=asrn; r[49]=cls; r[62]=str(channel)
    r[29]=str(erp_h); r[52]=str(erp_h); r[23]="380"; r[47]=str(rcamsl)
    _dms(r, lat, lon, 30, 34); return "|".join(r)
def tv_eng(fid, erp_eff, lat, lon, channel, asrn="", rec="C", rcamsl="500"):
    r=[""]*75; r[19]=rec; r[21]=str(fid); r[7]=asrn; r[66]=str(channel)
    r[15]=str(erp_eff); r[24]="400"; r[56]=str(rcamsl)
    _dms(r, lat, lon, 28, 32); return "|".join(r)
def am_eng(app_id, fid, rec="C"):
    r=[""]*17; r[1]=str(app_id); r[4]=str(fid); return "|".join(r)
def am_ant(app_id, power, lat, lon, rec="C"):
    r=[""]*41; r[2]=str(app_id); r[22]=str(power); r[27]=rec
    _dms(r, lat, lon, 12, 16); return "|".join(r)

with zipfile.ZipFile("fixtures/facility.zip","w") as z:
    z.writestr("facility.dat","\n".join([
        facility(90001,"KDEMO-FM","FM","LICEN",95.3,237),     # licensed FM near Diablo
        facility(90002,"KDTV",    "DT","LICEN","",12),        # licensed TV near Diablo
        facility(90003,"KAMX",    "AM","LICEN",740,""),       # licensed AM near Diablo (740 kHz)
        facility(90004,"KVOID-FM","FM","FVOID",97.7,249),     # VOID -> must be excluded
    ])+"\n")
with zipfile.ZipFile("fixtures/fm_eng_data.zip","w") as z:
    z.writestr("fm_eng_data.dat","\n".join([
        fm_eng(90001, 50, 37.8820, -121.9150, asrn="1234567", channel=237),   # links to ASR tower 1234567
        fm_eng(90001, 99, 37.8820, -121.9150, rec="A", channel=237),          # archived -> ignored (dedupe to 'C')
        fm_eng(90004, 6,  37.8810, -121.9150, channel=249),                   # facility is VOID -> excluded
    ])+"\n")
with zipfile.ZipFile("fixtures/tv_eng_data.zip","w") as z:
    z.writestr("tv_eng_data.dat","\n".join([
        tv_eng(90002, 300, 37.8818, -121.9140, 12),          # 300 kW DT, ch12 -> center 207 MHz
    ])+"\n")
with zipfile.ZipFile("fixtures/am_eng_data.zip","w") as z:
    z.writestr("am_eng_data.dat","\n".join([am_eng(700001, 90003)])+"\n")
with zipfile.ZipFile("fixtures/am_ant_sys.zip","w") as z:
    z.writestr("am_ant_sys.dat","\n".join([am_ant(700001, 5, 37.8800, -121.9100)])+"\n")

print("wrote fixtures/:", sorted(os.listdir("fixtures")))

# --------------------------------------------------------------------------- #
# Self-check assertions (run the real loaders against the fixtures)
# --------------------------------------------------------------------------- #
import sota_rf_sources as m
bc = m.load_broadcast("fixtures/facility.zip", "fixtures/fm_eng_data.zip",
                      "fixtures/tv_eng_data.zip", "fixtures/am_eng_data.zip",
                      "fixtures/am_ant_sys.zip")
assert set(bc["source_db"]) == {"FM", "TV", "AM"}, bc["source_db"].tolist()
assert len(bc) == 3, f"expected 3 broadcast rows (FVOID + archived excluded), got {len(bc)}"
assert not (bc["ref"] == "KVOID-FM").any(), "VOID facility was not excluded"
fm = bc[bc["source_db"] == "FM"].iloc[0]
assert abs(fm["max_power_w"] - 50000) < 1, f"FM ERP kW->W wrong: {fm['max_power_w']}"
assert fm["link_reg"] == "1234567", f"FM->ASR asrn link wrong: {fm['link_reg']}"
assert abs(float(fm["freqs_mhz"]) - 95.3) < 1e-6, fm["freqs_mhz"]
tv = bc[bc["source_db"] == "TV"].iloc[0]
assert abs(tv["max_power_w"] - 300000) < 1, tv["max_power_w"]
assert abs(float(tv["freqs_mhz"]) - 207.0) < 1e-6, f"TV ch12 center wrong: {tv['freqs_mhz']}"
am = bc[bc["source_db"] == "AM"].iloc[0]
assert abs(float(am["freqs_mhz"]) - 0.74) < 1e-6, f"AM kHz->MHz wrong: {am['freqs_mhz']}"
assert abs(am["max_power_w"] - 5000) < 1, am["max_power_w"]
print("broadcast self-check OK:", len(bc), "rows  "
      f"(FM {fm['max_power_w']:.0f}W, TV {tv['max_power_w']:.0f}W @ {tv['freqs_mhz']}MHz, "
      f"AM {am['freqs_mhz']}MHz)")

# ---- Microwave drop-in: l_micro shares the HD/EN/LO/FR layout, but its FR
# records carry no power. A microwave license must parse to a valid freq with
# NaN power (not 0, not a crash), and cancelled licenses stay excluded. ----
import numpy as _np
uls = m.load_uls(["fixtures/l_LMcomm.zip"])
assert not (uls["ref"] == "5003").any(), "cancelled ULS license 5003 not excluded"
mwrows = uls[uls["ref"] == "5004"]
assert len(mwrows) == 1, f"microwave license missing (got {len(mwrows)} rows)"
mw = mwrows.iloc[0]
assert mw["services"] == "CF", f"microwave service code wrong: {mw['services']!r}"
assert abs(float(mw["freqs_mhz"]) - 6034.15) < 1e-3, f"microwave freq wrong: {mw['freqs_mhz']}"
assert _np.isnan(mw["max_power_w"]), f"microwave power should be NaN, got {mw['max_power_w']}"
print(f"microwave self-check OK: 6 GHz CF path parsed, power=NaN, freq={mw['freqs_mhz']}MHz")

# ---- CalTopo scoring helpers (pure functions feeding the summit-risk layer) ----
assert m.human_w(1_000_000) == "1.00 MW", m.human_w(1_000_000)
assert m.human_w(45_000) == "45 kW", m.human_w(45_000)
assert m.human_w(250) == "250 W", m.human_w(250)
assert m.human_w(None) == "—" and m.human_w(float("nan")) == "—"
# risk tiers calibrated to San Bruno's real scores (70cm 37.6, 2m 11.5, 23cm 1.5, clear 0)
assert m._risk_tier(37.6) == "HIGH" and m._risk_tier(11.5) == "MODERATE"
assert m._risk_tier(1.5) == "LOW" and m._risk_tier(0.0) == "CLEAR"
# power->colour: no ERP is neutral grey; more power is redder (higher R channel)
assert m.power_to_hex(None) == "#8a97a3", m.power_to_hex(None)
assert m.power_to_hex(1_000_000)[1:3] > m.power_to_hex(200)[1:3], "1 MW should be redder than 200 W"
print("CalTopo scoring self-check OK: human_w / risk tiers / power heat-colour")
