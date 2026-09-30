# Kanalların yayın akışını toplayıp data.json dosyasına yazar.
# GitHub Actions tarafından her gün otomatik çalıştırılır.

import json, re, datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup

KANALLAR = [
    {"id": "kanald", "ad": "Kanal D",  "url": "https://www.kanald.com.tr/yayin-akisi"},
    {"id": "show",   "ad": "Show TV",  "url": "https://www.showtv.com.tr/yayin-akisi", "tip": "haftalik_link"},
    {"id": "atv",    "ad": "ATV",      "url": "https://www.atv.com.tr/yayin-akisi"},
    {"id": "star",   "ad": "Star TV",  "url": "https://www.startv.com.tr/yayin-akisi"},
    {"id": "now",    "ad": "NOW",      "url": "https://www.nowtv.com.tr/yayin-akisi"},
    {"id": "trt1",   "ad": "TRT 1",    "url": "https://www.trt1.com.tr/yayin-akisi"},
    {"id": "tv8",    "ad": "TV8",      "url": "https://www.tv8.com.tr/yayin-akisi"},
]

TZ = ZoneInfo("Europe/Istanbul")
BUGUN = datetime.datetime.now(TZ).date()
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept-Language": "tr-TR,tr;q=0.9",
}

SAAT_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")
SATIR_RE = re.compile(r"^(\d{1,2})[:.](\d{2})\s+(.{2,120})$")
ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")
BOLUM_RE = re.compile(r"(\d{1,4})\s*\.?\s*b[öo]l[üu]m", re.I)
BASLIK_ANAHTAR = ["title", "name", "programname", "programtitle", "program", "baslik", "programadi"]


def etiket_bul(metin):
    m = (metin or "").lower()
    durum = None
    if "yeni bölüm" in m or "yeni bolum" in m or "ilk bölüm" in m:
        durum = "yeni"
    elif "tekrar" in m:
        durum = "tekrar"
    elif "canlı" in m or "canli" in m:
        durum = "canli"
    bolum = BOLUM_RE.search(metin or "")
    return durum, int(bolum.group(1)) if bolum else None


def saat_ok(h, dk):
    return 0 <= h <= 23 and 0 <= dk <= 59


# ---------- 1. Yol: sayfanın içindeki JSON verisi ----------
def json_listeleri(obj):
    if isinstance(obj, list):
        if len(obj) >= 4 and all(isinstance(x, dict) for x in obj):
            yield obj
        for x in obj:
            yield from json_listeleri(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from json_listeleri(v)


def json_ogesi(d):
    baslik, saat, tarih, metin = None, None, None, []
    for k, v in d.items():
        kl = str(k).lower()
        if isinstance(v, str):
            metin.append(v)
            if baslik is None and kl in BASLIK_ANAHTAR and 1 < len(v) < 120:
                baslik = v.strip()
            iso = ISO_RE.search(v)
            if saat is None and iso and ("start" in kl or "bas" in kl or "time" in kl or "date" in kl or "saat" in kl):
                y, a, g, h, dk = map(int, iso.groups())
                tarih = datetime.date(y, a, g)
                saat = (h, dk)
            elif saat is None and ("start" in kl or "saat" in kl or "time" in kl):
                s = SAAT_RE.match(v.strip())
                if s and saat_ok(int(s.group(1)), int(s.group(2))):
                    saat = (int(s.group(1)), int(s.group(2)))
        elif isinstance(v, bool) and v:
            if "new" in kl or "yeni" in kl:
                metin.append("yeni bölüm")
            if "repeat" in kl or "tekrar" in kl or "rerun" in kl:
                metin.append("tekrar")
            if "live" in kl or "canli" in kl:
                metin.append("canlı")
        elif isinstance(v, int) and ("episode" in kl or "bolum" in kl) and 0 < v < 5000:
            metin.append(f"{v}. bölüm")
    if not baslik or not saat:
        return None
    durum, bolum = etiket_bul(" ".join(metin))
    return {"tarih": tarih, "saat": saat, "baslik": baslik, "durum": durum, "bolum": bolum}


def jsondan_cikar(soup):
    en_iyi = []
    for s in soup.find_all("script"):
        txt = (s.string or s.get_text() or "").strip()
        if not txt:
            continue
        adaylar = []
        if txt[0] in "[{":
            adaylar.append(txt)
        m = re.search(r"=\s*(\{.*\})\s*;?\s*$", txt, re.S)
        if m:
            adaylar.append(m.group(1))
        for a in adaylar:
            try:
                veri = json.loads(a)
            except Exception:
                continue
            for liste in json_listeleri(veri):
                ogeler = [o for o in (json_ogesi(d) for d in liste) if o]
                if len(ogeler) >= 4:
                    yarin = BUGUN + datetime.timedelta(days=1)
                    tarihli = [o for o in ogeler if o["tarih"] == BUGUN
                               or (o["tarih"] == yarin and o["saat"][0] < 6)]
                    if tarihli:
                        ogeler = tarihli
                    if len(ogeler) > len(en_iyi):
                        en_iyi = ogeler
    return en_iyi


# ---------- 2. Yol: sayfadaki görünen yazılar ----------
def metinden_cikar(soup):
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    satirlar = [s.strip() for s in soup.get_text("\n").split("\n") if s.strip()]
    ogeler = []
    i = 0
    while i < len(satirlar):
        s = satirlar[i]
        m1, m2 = SAAT_RE.match(s), SATIR_RE.match(s)
        if m1 and i + 1 < len(satirlar) and not SAAT_RE.match(satirlar[i + 1]):
            h, dk, baslik = int(m1.group(1)), int(m1.group(2)), satirlar[i + 1]
            ek = " ".join(satirlar[i + 2:i + 5])
            i += 2
        elif m2:
            h, dk, baslik = int(m2.group(1)), int(m2.group(2)), m2.group(3)
            ek = " ".join(satirlar[i + 1:i + 3])
            i += 1
        else:
            i += 1
            continue
        if not saat_ok(h, dk) or len(baslik) < 2:
            continue
        durum, bolum = etiket_bul(baslik + " " + ek)
        ogeler.append({"tarih": None, "saat": (h, dk), "baslik": baslik, "durum": durum, "bolum": bolum})

    # Sayfada birden fazla gün varsa sadece ilk günü al:
    # yayın günü sabah başlar, gece yarısını geçer (1 geri dönüş normal), 2. geri dönüş = yeni gün
    sonuc, donus, onceki = [], 0, None
    for o in ogeler:
        dakika = o["saat"][0] * 60 + o["saat"][1]
        if onceki is not None:
            if dakika < onceki - 60:
                donus += 1
                if donus >= 2 or dakika >= 6 * 60:
                    break
            elif donus >= 1 and onceki < 6 * 60 <= dakika:
                break
        sonuc.append(o)
        onceki = dakika
    return sonuc


# ---------- 3. Yol: haftalık, bağlantı kartlı sayfalar (Show TV gibi) ----------
# Kart yazısı örneği: "Siyah Kalp Tekrar 06:00 Siyah Kalp İzle"
KART_RE = re.compile(r"(?:(yeni bölüm|tekrar|canlı)[^0-9]{0,15})?(\d{2}):(\d{2})\s+\S", re.I)


def gunlere_bol(ogeler, gun_basi=5 * 60):
    """Arka arkaya dizilmiş haftalık listeyi günlere böler."""
    gunler, gun, gece, onceki = [], [], False, None
    for o in ogeler:
        dakika = o["saat"][0] * 60 + o["saat"][1]
        yeni_gun = False
        if onceki is not None:
            if dakika < onceki:              # saat geriye gitti
                if dakika >= gun_basi:
                    yeni_gun = True
                else:
                    gece = True               # gece yarısını geçtik
            elif gece and dakika >= gun_basi:
                yeni_gun = True
        if yeni_gun:
            gunler.append(gun)
            gun, gece = [], False
        gun.append(o)
        onceki = dakika
    if gun:
        gunler.append(gun)
    return gunler


def haftalik_link_cikar(soup):
    ogeler = []
    for a in soup.find_all("a", title=True):
        metin = a.get_text(" ", strip=True)
        m = KART_RE.search(metin)
        if not m:
            continue
        h, dk = int(m.group(2)), int(m.group(3))
        if not saat_ok(h, dk):
            continue
        etiket = (m.group(1) or "").lower()
        durum = {"yeni bölüm": "yeni", "tekrar": "tekrar", "canlı": "canli"}.get(etiket)
        ogeler.append({"tarih": None, "saat": (h, dk), "baslik": a["title"].strip(),
                       "durum": durum, "bolum": None})
    gunler = gunlere_bol(ogeler)
    if len(gunler) == 7:                      # hafta Pazartesi'den başlıyor
        return gunler[BUGUN.weekday()]
    return gunler[0] if gunler else []


def temizle(ogeler):
    gorulen, sonuc = set(), []
    for o in ogeler:
        baslik = re.sub(r"\s+", " ", o["baslik"]).strip()
        baslik = re.sub(r"\((yeni bölüm|tekrar|canlı)\)", "", baslik, flags=re.I).strip(" -–")
        anahtar = (o["saat"], baslik.lower())
        if anahtar in gorulen:
            continue
        gorulen.add(anahtar)
        sonuc.append({
            "saat": f"{o['saat'][0]:02d}:{o['saat'][1]:02d}",
            "baslik": baslik,
            "durum": o["durum"],
            "bolum": o["bolum"],
        })
    sonuc.sort(key=lambda x: (int(x["saat"][:2]) + (24 if int(x["saat"][:2]) < 6 else 0), x["saat"][3:]))
    return sonuc


def tahmin_et(ogeler, gecmis, kanal_id):
    """Etiket yoksa: bölüm numarası artmışsa ya da akşam kuşağındaki ilk yayınsa 'muhtemelen yeni'."""
    ilk_aksam = {}
    for o in ogeler:
        h = int(o["saat"][:2])
        if 19 <= h <= 23 and o["baslik"].lower() not in ilk_aksam:
            ilk_aksam[o["baslik"].lower()] = o["saat"]
    sayac = {}
    for o in ogeler:
        sayac[o["baslik"].lower()] = sayac.get(o["baslik"].lower(), 0) + 1

    for o in ogeler:
        anahtar = f"{kanal_id}|{o['baslik'].lower()}"
        if o["bolum"]:
            eski = gecmis.get(anahtar, 0)
            if o["durum"] is None and eski and o["bolum"] > eski:
                o["durum"] = "yeni"
            if o["durum"] == "yeni" or o["bolum"] > eski:
                gecmis[anahtar] = max(eski, o["bolum"])
        if o["durum"] is None:
            b = o["baslik"].lower()
            if sayac[b] > 1:
                o["durum"] = "tahmin" if ilk_aksam.get(b) == o["saat"] else "tekrar"


def main():
    try:
        with open("gecmis.json", encoding="utf-8") as f:
            gecmis = json.load(f)
    except Exception:
        gecmis = {}

    kanallar = []
    for k in KANALLAR:
        kayit = {"id": k["id"], "ad": k["ad"], "kaynak": k["url"], "ok": False, "hata": None, "program": []}
        try:
            r = requests.get(k["url"], headers=HEADERS, timeout=30)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            if k.get("tip") == "haftalik_link":
                ogeler = haftalik_link_cikar(soup)
            else:
                ogeler = jsondan_cikar(soup) or metinden_cikar(soup)
            program = temizle(ogeler)
            tahmin_et(program, gecmis, k["id"])
            kayit["program"] = program
            kayit["ok"] = len(program) >= 3
            if not kayit["ok"]:
                kayit["hata"] = "Sayfada yayın akışı bulunamadı"
        except Exception as e:
            kayit["hata"] = f"{type(e).__name__}: {str(e)[:150]}"
        print(f"{k['ad']}: {'OK' if kayit['ok'] else 'HATA'} ({len(kayit['program'])} program) {kayit['hata'] or ''}")
        kanallar.append(kayit)

    veri = {
        "tarih": BUGUN.isoformat(),
        "guncellendi": datetime.datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        "kanallar": kanallar,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=1)
    with open("gecmis.json", "w", encoding="utf-8") as f:
        json.dump(gecmis, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
