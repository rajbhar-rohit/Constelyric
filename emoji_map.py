"""
Word -> emoji for Constelyric's constellation style.

Each line is  emoji: (animation, "space separated words in many languages").
To add words, just append them (lower-case, accents optional).  Users can also add their own in
%APPDATA%\\Constelyric\\emoji.json  like  {"tumhare": "🫶", "samundar": "🌊"}.

Animations: wobble, pulse, flicker, spin, twinkle, drop, bounce, float
Languages covered: English, Hindi/Urdu/Punjabi (romanised + Devanagari), Spanish, French,
Portuguese, German, Italian, Turkish, Russian, Arabic, Korean, Japanese, Chinese.
"""
import json
import os
import re
import unicodedata
from pathlib import Path

CONCEPTS = {
    "👀": ("wobble", "eyes eye aankhein aankhen aankhon aankh ankhein ankhen ankhon ankh nazar nazrein nazron nain nainon nayan "
                     "akhan आँखें आंखें आँखों आंखों आँख आंख नज़र नजर नैन ojos ojo yeux oeil olhos olho augen auge occhi occhio "
                     "눈동자 눈 眼睛 眼 目 瞳 عيون عين gozler goz göz глаза глаз kann kallu chokh"),
    "❤️": ("pulse", "heart hearts dil dilon dilbar dhadkan dhadkane धड़कन दिल दिलों corazon corazones coeur coeurs coracao "
                    "herz herzen cuore cuori 마음 심장 心 قلب kalp сердце сердца"),
    "💞": ("pulse", "love loves loved lover ishq ishk pyar pyaar mohabbat muhabbat mohabbatein prem prema इश्क़ इश्क प्यार प्रेम "
                    "मोहब्बत amor amore amour liebe lieben 사랑 愛 爱 حب عشق любовь любви"),
    "💔": ("pulse", "heartbreak broken breakup judai judaai जुदाई bewafa bewafaa बेवफा dard दर्द hurt hurts hurting sad udaas उदास "
                    "dolor douleur dor dolore schmerz 아픔 痛 伤 ألم acı боль"),
    "🔥": ("flicker", "fire flame flames burn burning burns aag agni aatish jwala आग अग्नि fuego llama feu flamme fogo fogueira "
                      "feuer fiamma fuoco 불 火 炎 نار النار ateş огонь пламя"),
    "🌙": ("float", "moon chand chaand chanda चाँद चांद चंद्रमा luna lune lua mond 달 月 月亮 قمر ay луна"),
    "☀️": ("spin", "sun sunshine suraj surya sooraj सूरज सूर्य sol soleil sonne sole 해 태양 太阳 日 شمس güneş солнце"),
    "⭐": ("twinkle", "star stars sitara sitare taare taaray tara सितारा सितारे तारे तारा estrella estrellas etoile etoiles estrela "
                      "stern sterne stella stelle 별 星 星星 نجمة نجوم yıldız звезда звезды"),
    "🌌": ("float", "sky skies heaven heavens asmaan aasman aasmaan gagan jannat आसमान आसमाँ गगन जन्नत cielo ciel ceu céu himmel "
                    "하늘 空 天 سماء gök небо"),
    "🌃": ("float", "night nights raat raatein raaton shab रात रातें noche nuit noite nacht notte 밤 夜 夜晚 ليل gece ночь "
                    "dark darkness andhera andhere अंधेरा"),
    "🌅": ("float", "morning sunrise dawn subah subha savera सुबह सवेरा manana mañana matin manhã morgen mattina 아침 朝 早晨 "
                    "صباح sabah утро"),
    "😢": ("drop", "tears tear cry crying cried aansu aansoo ashq आँसू आंसू lagrimas lágrimas larmes tranen tränen lacrime "
                   "눈물 涙 泪 眼泪 دموع gözyaşı слёзы слезы"),
    "🌧️": ("drop", "rain raining rainy storm baarish barish barsaat barsat बारिश बरसात बादल baadal badal clouds cloud lluvia pluie "
                   "chuva regen pioggia 비 雨 مطر yağmur дождь toofan तूफान"),
    "🌊": ("float", "ocean sea waves wave samundar samandar sagar darya dariya समंदर समुद्र दरिया mar mer mare meer oceano "
                    "océano 바다 海 بحر deniz море океан"),
    "🍃": ("float", "wind breeze hawa hawaa हवा pawan पवन viento vent vento luft 바람 風 风 ريح رياح rüzgar ветер"),
    "⚡": ("flicker", "lightning thunder bijli bijlee बिजली kadak rayo relampago relámpago foudre éclair blitz donner fulmine "
                      "tuono 번개 雷 闪电 برق şimşek молния гроза"),
    "❄️": ("drop", "snow winter baraf barf बर्फ thand thandi ठंड ठंडी cold nieve invierno neige hiver neve inverno schnee "
                   "雪 雪花 ثلج kış снег зима"),
    "🕊️": ("float", "bird birds fly flying flies flew wings wing soar parinda parindey panchhi udna udaan pankh पंख परिंदा पंछी "
                    "उड़ान ala alas aile ailes asa asas flügel 날개 羽 翼 جناح kanat крылья"),
    "💃": ("bounce", "dance dancing danced naach naacho naachna नाच baile bailar danse danca dança tanz tanzen ballo danza "
                     "춤 踊 跳舞 رقص dans танец"),
    "🎶": ("bounce", "music song songs sing singing gaana gaane geet sangeet raag ghazal गाना गीत संगीत musica música musique "
                     "chanson canción cancion canção lied musik canzone 노래 음악 歌 音楽 音乐 موسيقى اغنية şarkı песня музыка "
                     "melody dhun धुन"),
    "💋": ("bounce", "kiss kisses kissing chumma chumban चूमना चुम्बन beso baiser beijo kuss bacio 키스 キス 吻 قبلة öpücük поцелуй"),
    "😊": ("bounce", "smile smiling smiles muskaan muskurahat मुस्कान मुस्कुराहट sonrisa sourire sorriso lächeln 미소 웃음 笑 微笑 "
                     "ابتسامة gülümseme улыбка hasi hasna हंसी"),
    "👑": ("float", "crown king queen raja rani badshah baadshah shahenshah राजा रानी बादशाह corona rey reina roi reine rei "
                    "rainha könig königin regina 왕 王 皇 ملك ملكة kral король королева"),
    "👻": ("float", "ghost ghosts spirit bhoot bhut भूत rooh ruh रूह आत्मा fantasma fantome fantôme geist 귀신 幽霊 鬼 幽灵 شبح "
                    "hayalet призрак"),
    "💀": ("flicker", "skull dead death dying maut mout marna मौत मरना kabr grave muerte muerto mort morte 죽음 死 亡 موت "
                      "ölüm смерть"),
    "🌸": ("float", "flower flowers rose roses bloom blossom phool phoolon phul gulab gulaab फूल गुलाब kali कली flor flores fleur "
                    "fleurs blume blumen fiore fiori 꽃 花 花朵 زهرة çiçek цветок"),
    "💎": ("twinkle", "diamond diamonds gem gems jewel jewels heera heere hira हीरा moti मोती diamante diamant 보석 다이아몬드 "
                      "钻石 宝石 ダイヤ الماس elmas бриллиант алмаз"),
    "✨": ("twinkle", "shine shining sparkle sparkling magic jadoo jaadu जादू chamak chamakna चमक roshni रोशनी रौशनी light lights "
                      "luz lumiere lumière luce licht 빛 光 نور ضوء ışık свет"),
    "💭": ("float", "dream dreams dreaming sapna sapne sapno khwab khwaab khwaabon ख्वाब सपना सपने sueno sueño sonho sonhos "
                    "reve rêve reves rêves traum träume sogno sogni 꿈 夢 梦 حلم rüya мечта"),
    "🕰️": ("spin", "time clock hours hour waqt samay वक़्त वक्त समय tiempo temps tempo zeit 시간 時間 时间 وقت zaman время"),
    "🏠": ("bounce", "home house ghar घर hogar casa maison zuhause heim 집 家 منزل البيت ev дом"),
    "🛣️": ("float", "road roads journey travel raasta raste raah rasta safar सफर रास्ता राह camino chemin caminho strasse straße "
                    "strada 길 道 路 طريق yol дорога"),
    "🌍": ("spin", "world duniya dunya दुनिया jahan jahaan जहाँ जहां mundo monde welt mondo 세상 世界 عالم dünya мир"),
    "🌈": ("float", "rainbow colors colours color colour rang rangon रंग रंगों colores couleur cores farbe farben colori 색 色 "
                    "彩虹 لون renk цвет"),
    "🍷": ("bounce", "wine drunk drink drinks sharab sharaab daaru daru शराब दारू nasha नशा vino vin vinho wein 술 酒 خمر şarap вино"),
    "😴": ("float", "sleep sleeping sleepy neend nind neendein neendon नींद dormir sommeil dormire schlaf schlafen sonno 잠 眠 睡 "
                    "نوم uyku"),
    "🙏": ("pulse", "pray prayer dua duaa दुआ prarthana प्रार्थना ibadat इबादत bhagwan bhagwaan भगवान khuda ख़ुदा खुदा rab रब "
                    "allah god dios dieu deus gott dio 신 神 上帝 الله tanrı бог"),
    "🤝": ("bounce", "hand hands haath haathon हाथ हाथों mano manos mao mão maos mãos hände hande 손 手 يد elim рука"),
}

_STYLE_OF = {e: s for e, (s, _) in CONCEPTS.items()}


def norm(word):
    """Lower-case, drop accents and punctuation (works for any script)."""
    w = unicodedata.normalize("NFKD", word.lower().replace("œ", "oe").replace("ß", "ss"))
    w = "".join(c for c in w if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", "", w)


def _is_cjk(c):
    o = ord(c)
    return 0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x9FFF


_INDEX, _CJK = {}, []
for _emoji, (_style, _words) in CONCEPTS.items():
    for _w in _words.split():
        _k = norm(_w)
        if _k:
            _INDEX.setdefault(_k, (_emoji, _style))
            if any(_is_cjk(c) for c in _k):
                _CJK.append((_k, (_emoji, _style)))

# user additions
try:
    _user = Path(os.environ.get("APPDATA", str(Path.home()))) / "Constelyric" / "emoji.json"
    for _w, _e in json.loads(_user.read_text(encoding="utf-8")).items():
        _INDEX[norm(_w)] = (_e, _STYLE_OF.get(_e, "float"))
except Exception:
    pass


def emoji_for(word):
    """-> (emoji, animation) or None."""
    k = norm(word)
    if not k:
        return None
    if k in _INDEX:
        return _INDEX[k]
    for suf in ("s", "es", "ing", "ed"):                 # simple English endings
        if k.endswith(suf) and len(k) > len(suf) + 2 and k[:-len(suf)] in _INDEX:
            return _INDEX[k[:-len(suf)]]
    if any(_is_cjk(c) for c in k):                        # Chinese/Japanese have no spaces
        for key, hit in _CJK:
            if key in k:
                return hit
    return None
