# Vermögen – Design-Richtung

Abgeleitet aus [pbakaus/impeccable](https://github.com/pbakaus/impeccable) (SKILL, `operate.md`,
`craft-floor.md`, `typeset.md`, `colorize.md`, `layout.md`, `animate.md`, `adapt.md`, `clarify.md`,
`harden.md`, `onboard.md`). Maßgeblich für alles unter `frontend/`. Tokens leben in
`frontend/src/styles/tokens.css`; dieses Dokument erklärt, warum sie so sind.

## 1. Einordnung

**Modus: Operate.** Ein einzelner Nutzer öffnet die App am Schreibtisch (abends, oft dunkel) oder kurz
am Handy, um drei Fragen zu beantworten: *Wo stehe ich? Was hat sich geändert? Was steht an?* – und um
Belege zu verbuchen. Erfolg heißt: Zahl in unter zwei Sekunden gefunden, Beleg in unter einer Minute
übernommen. Ambition zeigt sich als Präzision und Zurückhaltung, nicht als Effekt.

Impeccables Operate-Regel: Die „Welt“ leiht genau vier Dinge – **Schrift, Palette, Dichte und einen
Signature Move**. Layout, Navigation und Controls bleiben die bekannten Web-Standards.

| Leihgabe | Entscheidung |
|---|---|
| Schrift | IBM Plex Sans (variabel, selbst gehostet), eine Familie für alles |
| Palette | „Kontor“: warmes Papier, petrolfarbene Tinte, Ocker als Zweitfarbe; Grün/Rot ausschließlich für Gewinn/Verlust |
| Dichte | Kompakt: 13-px-Tabellen, 36-px-Zeilen, 15-px-Fließtext |
| Signature Move | Die **Vermögensleiste**: ein schmaler, segmentierter Balken, der überall dieselbe Frage beantwortet – „woraus besteht diese Zahl?“ (Konten im Nettovermögen, direkt/über ETF in der Durchschau, Einnahmen/Ausgaben im Haushaltsbuch, Abdeckung der ETF-Daten). Er ersetzt die Hero-Metric-Schablone und dekorative Sparklines. |

## 2. Typografie

* **Familie:** `IBM Plex Sans Variable` (`@fontsource-variable/ibm-plex-sans`, gebündelt, kein CDN).
  Fallback `system-ui, "Segoe UI", sans-serif`. Kein zweiter Font, keine Monospace-Kostümierung –
  Zahlen bekommen `font-variant-numeric: tabular-nums` statt einer Mono-Schrift.
* **Feste rem-Skala** (kein `clamp`, Verhältnis ≈ 1,15 – Operate-Regel „tighter ratio“):

| Token | Größe | Rolle |
|---|---|---|
| `--fs-xs` | 0.75rem (12) | Metadaten, Achsen, Badges |
| `--fs-sm` | 0.8125rem (13) | Tabellen, Labels, Buttons |
| `--fs-md` | 0.9375rem (15) | Fließtext, Formularfelder |
| `--fs-lg` | 1.0625rem (17) | Abschnittsüberschrift (h2) |
| `--fs-xl` | 1.375rem (22) | Seitentitel (h1), KPI-Werte |
| `--fs-2xl` | 2rem (32) | genau eine Leitzahl pro Seite (z. B. Nettovermögen) |

* Gewichte: 400 Text, 500 Labels/Tabellenkopf/Navigation, 600 Überschriften und Leitzahlen. Hierarchie
  entsteht aus Größe **und** Gewicht **und** Ton (`--text`, `--text-2`, `--text-3`), nie aus Größe allein.
* Überschriften `text-wrap: balance`, Erklärtexte `text-wrap: pretty`, max. 68ch.
* Zahlen: immer rechtsbündig in Tabellen, `tabular-nums`, de-DE via `Intl` (`1.234,56 €`, `+3,2 %`,
  `12.11.2026`). Vorzeichen werden ausgeschrieben (`+`/`−`, echtes Minus U+2212).
* Kein Uppercase-Kicker über Überschriften (Impeccable-Ban „eyebrow“). Tabellenköpfe sind
  normal gesetzt, 500, `--text-2`.

## 3. Farbe

Strategie **Restrained mit committeter Shell**: Die Navigationsleiste trägt die Markenfarbe
(Petrol-Tinte) als Fläche; der Inhalt bleibt ruhig auf warmem Papier. Akzent nur für primäre Aktion,
Auswahl, Fokus und Links. Grün/Rot sind für Gewinn/Verlust reserviert und werden nie dekorativ verwendet.

### Rollen-Tokens

| Token | Hell | Dunkel | Rolle |
|---|---|---|---|
| `--bg` | `#f6f4ef` | `#101518` | Seitenhintergrund (warmes Papier / Tintenschwarz) |
| `--surface` | `#fffefb` | `#171d20` | Panels, Tabellen, Formulare |
| `--surface-2` | `#efece5` | `#1e262a` | Tabellenkopf, Hover, eingelassene Flächen |
| `--border` | `#dedad0` | `#2a3438` | Trennlinien |
| `--border-strong` | `#bdb7a8` | `#43515a` | Feldrahmen (3:1 gegen Fläche) |
| `--text` | `#1c2226` | `#e6eaeb` | Primärtext |
| `--text-2` | `#4d565c` | `#aab4b8` | Sekundärtext, Labels |
| `--text-3` | `#676f74` | `#8a959a` | Metadaten (≥ 4,5:1 auf `--bg`/`--surface`) |
| `--rail-bg` | `#12303a` | `#0b1d23` | Sidebar / Bottom-Nav-Aktivfläche |
| `--rail-text` / `-dim` | `#e3ecee` / `#a3b9bf` | `#dbe6e9` / `#93aab0` | Text auf der Shell (aus dem Petrol getönt, kein Grau) |
| `--accent` | `#14607a` | `#62b6d2` | Primäraktion, Auswahl, Links, Fokus |
| `--accent-soft` | `#dcebf0` | `#163842` | ausgewählte Zeile/Tab, Info-Hinweis |
| `--gain` / `--gain-soft` | `#17734a` / `#e0f0e6` | `#52c48c` / `#14301f` | positiv |
| `--loss` / `--loss-soft` | `#b3361f` / `#f8e3dd` | `#f08670` / `#3d1f19` | negativ, Fehler |
| `--warn` / `--warn-soft` | `#855600` / `#f6ebcf` | `#e2b354` / `#382c10` | Schätzung, niedrige Konfidenz, veraltete Daten |

Dunkel ist eigenständig komponiert (Flächen werden nach oben heller, Akzent entsättigt und aufgehellt),
nicht invertiert. Umschaltung: `prefers-color-scheme` + manueller Schalter (`data-theme` am `<html>`).

### Gewinn/Verlust ohne Farbe lesbar

1. Vorzeichen `+` / `−` steht immer am Wert.
2. Gerichtetes Dreieck (SVG, ▲ nach oben / ▼ nach unten) vor Prozentwerten.
3. In Charts: Verlustflächen zusätzlich unterhalb der Nulllinie, Schätzungen gestrichelt.
4. Grün `#17734a` und Rot `#b3361f` unterscheiden sich deutlich in der Helligkeit-/Blau-Gelb-Achse
   nicht genug für Deuteranopie – deshalb sind 1–3 Pflicht, Farbe ist nur Verstärker.

### Chart-Palette (kategorial, in dieser Reihenfolge)

| # | Name | Hell | Dunkel |
|---|---|---|---|
| 1 | Petrol | `#14607a` | `#62b6d2` |
| 2 | Ocker | `#bd861b` | `#e2b354` |
| 3 | Terrakotta | `#b4553d` | `#e08e73` |
| 4 | Salbei | `#5b8a58` | `#8fc48a` |
| 5 | Stahlblau | `#6c8db0` | `#93b4dc` |
| 6 | Rosenholz | `#93536a` | `#cb8ea5` |
| 7 | Oliv | `#7b783a` | `#b8b56c` |
| 8 | Schiefer | `#79838a` | `#9ba6ab` |

Regeln: max. 8 Kategorien, Rest wird „Sonstige“ (Schiefer). Zeitreihen: Hauptserie Petrol 2 px,
Vergleich/Einstand Ocker gestrichelt, Bänder (p10–p90, KGV-Band) als 12–18 % Deckkraft der Serienfarbe.
Gitterlinien nur horizontal in `--border`, keine Achsenlinien-Kästen, Achsentext `--text-3` 12 px.
Legenden nur bei > 1 Serie; sonst steht die Bedeutung in der Überschrift. Jeder Chart hat ein
`aria-label` und die Daten stehen zusätzlich als Tabelle oder Kennzahl auf der Seite.

## 4. Raum & Layout

* **4-px-Basis:** `--sp-1` 4 · `--sp-2` 8 · `--sp-3` 12 · `--sp-4` 16 · `--sp-5` 24 · `--sp-6` 32 · `--sp-7` 48.
  Rhythmus: eng innerhalb einer Gruppe (4–8), 16 in Panels, 32 zwischen Abschnitten; über einer
  Überschrift mehr Raum als darunter.
* **Radius:** 4 px Controls, 8 px Panels, Pillen nur für Status-Badges. **Tiefe:** Panels haben eine
  1-px-Border und *keinen* Schatten; Schatten (versetzt, weich) nur für schwebende Ebenen (Menü, Sheet).
* **Shell:** ≥ 1100 px Sidebar 232 px (Petrol) · 720–1099 px Icon-Rail 64 px · < 720 px Kopfzeile +
  Bottom-Navigation (4 Ziele + „Mehr“-Sheet mit allen Seiten). Inhalt max. 1320 px.
* **Panels statt Kartenraster:** Ein Abschnitt = Überschrift + Inhalt. Panels werden nie verschachtelt;
  innerhalb gliedern Linien und Abstand. KPI-Zeilen sind eine durch Linien geteilte Leiste, keine
  Reihe identischer Karten.
* **Tabellen:** 36-px-Zeilen (44 px Touch auf Mobil), Zebra nein, Hover ja, Kopf sticky, Zahlen rechts.
  Mobil (< 720 px): Spalten werden priorisiert (`data-prio`), Nebenwerte rutschen als zweite Zeile unter
  den Namen; nie horizontales Scrollen für Kernspalten.
* **Formulare:** Label immer sichtbar über dem Feld, Platzhalter sind Beispiele. Fehler stehen am Feld.

## 5. Bewegung

150–200 ms, `cubic-bezier(0.16, 1, 0.3, 1)`, nur für Zustandswechsel (Hover, Aufklappen, Sheet, Theme).
Keine Seiten-Ladechoreografie, keine Scroll-Reveals. Skeletons pulsieren dezent. Unter
`prefers-reduced-motion: reduce` sind Transitionen und Chart-Animationen aus.

## 6. Komponentenregeln

* **Buttons:** primär (Akzentfläche) höchstens einmal pro Ansicht; sekundär (Border); „ghost“ für
  Zeilenaktionen; destruktiv in `--loss` mit benannter Aktion („Konto löschen“, nie „OK“).
* **Zustände überall:** default, hover, focus-visible (2-px-Ring `--accent`, 2 px Abstand), active,
  disabled, loading. **Laden** = Skeleton in Form des Inhalts. **Leer** = ein Satz, was fehlt, und die
  nächste Aktion als Button/Link. **Fehler** = was fehlgeschlagen ist + „Erneut versuchen“.
* **Badges:** Text + Form; Status nie nur über Farbe (z. B. „Schätzung“ gestrichelter Rand).
* **Dialoge:** nur für destruktive Bestätigung. Erfassen/Bearbeiten passiert inline oder in einem
  seitlichen Panel (Desktop) bzw. Vollbild-Sheet (Mobil).
* **Icons:** `lucide-react`, 1,75 px Strich, 16/18 px; nie Emoji oder Unicode-Glyphen als Icon.
* **Browser-Flächen:** `::selection`, `caret-color`, `accent-color`, Scrollbar-Farbe und Fokusring
  kommen aus der Palette.
* **Privatmodus:** Schalter in der Kopfzeile; Beträge (`.amt`) werden weichgezeichnet, Chart-Achsen
  zeigen „•••“. Prozentwerte bleiben lesbar.

## 7. Sprache (UX-Text)

Deutsch, Du-frei und knapp („Transaktion erfassen“, „Alle übernehmen“). Buttons benennen Verb + Objekt.
Fehlermeldungen: was, warum (falls bekannt), wie weiter. Leere Zustände unterscheiden Erstnutzung
(„Noch keine Transaktionen – Dokument importieren oder manuell erfassen“) von „Filter ohne Treffer“.
Schätzwerte heißen „Schätzung“/„erwartet“, veraltete Kurse zeigen ihr Alter.

## 8. Bewusst vermiedene Anti-Patterns

| Anti-Pattern (Impeccable) | Stattdessen |
|---|---|
| Lila/Blau-Verläufe, Gradient-Text | Flächenfarben, Betonung über Gewicht/Größe |
| Glassmorphism, Blur als Deko | opake Flächen; Blur nur funktional im Privatmodus |
| Karten in Karten, identische Icon-Karten-Raster | Panels eine Ebene tief, Linien und Abstand |
| Hero-Metric-Schablone (große Zahl + Deko-Sparkline) | Leitzahl + Vermögensleiste mit echter Zusammensetzung |
| Eyebrow-/Kicker-Labels, nummerierte Abschnitte | Überschrift trägt allein |
| Farbige Seitenstreifen (`border-left`) an Karten/Alerts | Icon + getönte Fläche |
| Glow-/Halo-Schatten, „Ghost Card“ (Border + breiter Schatten) | Border *oder* Schatten |
| Inter/System-Font aus Gewohnheit, Mono als „Technik“-Kostüm | IBM Plex Sans, `tabular-nums` |
| Emoji/Unicode als Icons | lucide-Icons, eigene SVG-Dreiecke |
| Modal als Reflex | Inline-Formulare, Seitenpanel |
| Spinner mitten im Inhalt | Skeletons |
| Seiten-Einblend-Animationen, Bounce | 150–200 ms Zustandswechsel |
| Farbe als einziger Bedeutungsträger | Vorzeichen, Dreieck, Text, Strichart |
| Grauer Text auf Farbfläche | aus der Flächenfarbe getönter Text (`--rail-text-dim`) |
| Über-Rundung (Pillen-Buttons, 24-px-Radien) | 4/8 px |
