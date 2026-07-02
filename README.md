# Nirman Workshop Rating Extractor

Automates reading **handwritten workshop feedback forms** using Google Gemini 2.5 Flash Vision AI and exports the data to a structured, colour-coded Excel file.

---

## 📁 Folder Structure

```
Nirman Data Entry Automation/
│
├── workshop_extractor.py    ← Main script
├── requirements.txt         ← Python dependencies
│
├── forms/                   ← 📷 PUT YOUR SCANNED FORM IMAGES HERE
│   ├── form1.jpg
│   ├── form2.png
│   └── ...
│
├── workshop_ratings_output.xlsx  ← Generated Excel output
└── extraction_log.txt            ← Audit log
```

---

## ⚙️ Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Get a free Gemini API Key
- Visit: https://aistudio.google.com/apikey
- Create a free API key (no credit card required)

### 3. Configure the script
Open `workshop_extractor.py` and edit the top section:

```python
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY"   # ← Paste your key here
IMAGES_FOLDER  = "./forms"               # ← Folder with your scanned forms
```

### 4. Add your rating fields
If your form has different categories, edit the `RATING_FIELDS` dictionary:

```python
RATING_FIELDS = {
    "Content Quality":      {"min": 1, "max": 5,  "scale_label": "1-5"},
    "Trainer Performance":  {"min": 1, "max": 5,  "scale_label": "1-5"},
    "Overall Satisfaction": {"min": 1, "max": 10, "scale_label": "1-10"},
    # Add or remove fields as needed...
}
```

### 5. Place your images
Copy all scanned/photographed form images into the `forms/` folder.  
Supported formats: `.jpg`, `.jpeg`, `.png`, `.bmp`, `.tiff`, `.webp`

### 6. Run the script
```bash
python workshop_extractor.py
```

---

## 📊 Output Excel File

The generated `workshop_ratings_output.xlsx` contains **two sheets**:

### Sheet 1 — "Workshop Ratings"
| Column | Description |
|--------|-------------|
| File Name | Original image filename |
| Processed At | Timestamp of extraction |
| Content Quality | Extracted rating (number) |
| Trainer Performance | Extracted rating (number) |
| ... | Other rating columns |
| Review Required | `True` if any field needs manual check |
| Review Notes | Which specific fields are flagged |

**Colour coding:**
- 🟦 **Navy header** — column labels
- ⬜ **White** — clean, valid rating extracted
- 🟧 **Amber** — ILLEGIBLE / FIELD_MISSING / OUT_OF_RANGE
- 🟥 **Red** — API_ERROR / PARSE_ERROR (script/connection issue)
- 🟡 **Gold** — Review Required = True

### Sheet 2 — "Summary"
Average, Min, Max scores per category + count of errors across all forms.

---

## ⚠️ Error Sentinels

| Value | Meaning |
|-------|---------|
| `ILLEGIBLE` | AI could not confidently read the handwriting (<80% confidence) |
| `FIELD_MISSING` | Field was blank / not filled in on the form |
| `OUT_OF_RANGE` | AI read a number but it's outside the valid scale |
| `API_ERROR` | Gemini API call failed (check API key / internet) |
| `PARSE_ERROR` | AI returned malformed JSON (rare) |

All flagged rows are marked **Review Required = True** so you can filter and manually check them in Excel.

---

## 💡 Tips for Best Results
- Use **good lighting** when photographing forms
- Scan at **300 DPI or higher** for best OCR accuracy
- Ensure the full form is visible (not cropped)
- The AI reads the **entire page for context** before extracting values — scale labels and headers on the form help it understand the rating range automatically
