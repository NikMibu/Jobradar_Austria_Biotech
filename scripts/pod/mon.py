import sqlite3, json, sys
c = sqlite3.connect("data/heimspiel.db")
c.row_factory = sqlite3.Row
n = c.execute("select count(*) from postings").fetchone()[0]
reqrows = c.execute("select extracted_json from postings").fetchall()
nz = sum(1 for r in reqrows if json.loads(r[0]).get("requirements"))
fam = {}
for r in reqrows:
    f = json.loads(r[0])["role_family"]
    fam[f] = fam.get(f, 0) + 1
sc = c.execute("select count(*) from scores").fetchone()[0]
hp = c.execute("select count(*) from scores where hard_pass=1").fetchone()[0]
scored = c.execute("select count(*) from scores where fit_score is not null").fetchone()[0]
loc = c.execute("select count(*) from postings where site_id is not null").fetchone()[0]
tt = c.execute("select count(*) from travel_times").fetchone()[0]
print(f"extracted : {n}/785   with_requirements={nz}")
print(f"role_family: " + "  ".join(f"{k}={v}" for k, v in sorted(fam.items(), key=lambda x: -x[1])))
print(f"located   : {loc}   travel_times={tt}")
print(f"scores    : {sc}  (hard_pass={hp}, fit_scored={scored})")
if len(sys.argv) > 1 and sys.argv[1] == "top":
    for row in c.execute("""select p.extracted_json, s.fit_score, s.formal_status, s.practical_status, s.angle
                            from scores s join postings p on p.id=s.posting_id
                            where s.fit_score is not null order by s.fit_score desc limit 12"""):
        e = json.loads(row["extracted_json"])
        print(f"  {row['fit_score']:3}  {e['role_family']:16} {e['title_norm'][:45]:45} "
              f"F={row['formal_status']} P={row['practical_status']}")
