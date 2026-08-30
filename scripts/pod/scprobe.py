import sqlite3, json, time
from heimspiel import config, llm
from heimspiel.extract import Extraction
from heimspiel.match import score_one, compute_score

prof = config.load_profile()
c = sqlite3.connect("data/heimspiel.db"); c.row_factory = sqlite3.Row
rows = c.execute("""
    select p.extracted_json from postings p
    where json_extract(p.extracted_json,'$.role_family') in
      ('bioinformatics','data_science','scientific_software','downstream_process','lab_analytics','wet_lab_rnd')
    and json_array_length(json_extract(p.extracted_json,'$.requirements')) >= 3
    limit 3
""").fetchall()
for r in rows:
    ex = Extraction.model_validate_json(r["extracted_json"])
    t = time.time()
    assessment, fb = score_one(ex, prof)
    dt = time.time() - t
    cs = compute_score(ex, prof, assessment)
    print(f"{dt:5.1f}s  fit={cs.fit_score:3} conf={cs.confidence:3} fallback={fb}  "
          f"skills={len(assessment.skills)} rf={ex.role_family}  {ex.title_norm[:40]}")
