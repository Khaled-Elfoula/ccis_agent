# Demo script

Eight messages that show what the agent does and where each answer comes from.

Two doctors carry the demo:

| Doctor | Username | Department | Office | Email | Office hours in the DB |
|---|---|---|---|---|---|
| Dr. Saad Abdullah AlAhmadi (أ.د. سعد عبدالله الأحمدي) | `salahmadi` | CS | 31 / 2179 | salahmadi@ksu.edu.sa | yes (published) |
| Dr. Sami Alwakeel (سامي بن صالح بن أحمد الوكيل) | `swakeel` | CE | room 2206 | swakeel@ksu.edu.sa | none, so `data/sample_schedules.json` is used |

Name matching also handles the short English form: "Dr. Saad Al-Ahmadi" resolves to the same person as
the full name. A name that only partly matches comes back as **candidates, not confirmed**, and the
agent asks "did you mean X?" instead of guessing.

## The messages and what to expect

1. **"What programs does the college offer?"**
   `search_kb` returns passages from `ccis.ksu.edu.sa/ar/bsc`, `/masters`, `/phd`. The answer lists the
   programs and cites the source link.

2. **"ما هي برامج البكالوريوس في الكلية؟"**
   Same lookup, answered in Arabic with the source link.

3. **"How do I transfer to the college?"**
   `search_kb` on `acceptance-and-transfer`. The answer gives the transfer conditions from that page,
   with the link. Nothing invented.

4. **"What are the office and email of Dr. Saad Abdullah AlAhmadi?"**
   `find_contact` returns office 31 / 2179, salahmadi@ksu.edu.sa and the profile link
   `faculty.ksu.edu.sa/ar/salahmadi`.

5. **"And Dr. Saad Abdallah AlAhmadi?"** (misspelled)
   The same person. Name matching tolerates the missing "u" and returns the same contact details.

6. **"When is Dr. Sami Alwakeel free on Sunday?"**
   `get_free_slots` with `basis: sample_data`, because he has no published office hours. The answer
   says these times are demo sample data, not confirmed availability, gives Sunday 08:00-09:00 and
   11:00-16:00, and says nothing was booked.
   Ask the same question about Dr. Saad AlAhmadi to see the other path: `basis: office_hours`, the
   answer says these are his published office hours.

7. **"I want to withdraw from a course after the deadline."**
   A hand-off: the reason, the College Office for Educational and Academic Affairs
   (`ccis.ksu.edu.sa/ar/student-affairs-agency-mail`), and a ready email template with
   `[Your name]` and `[Your ID]`. That office has no published email, so the answer says the email
   was not found and gives the page link.

8. **"What's the cafeteria menu?"**
   Out of scope. The agent declines and says what it can help with. No tool call, no invented answer.

## Commands

```bash
py -m scrapers.run                        # refresh the college pages
py -m scrapers.run --faculty --resume       # refresh faculty lists and profiles
py scripts/report_faculty.py                # data report: counts, coverage, contacts
py -m pytest -q                            # the whole test suite
py scripts/demo_check.py                   # run the eight messages through the real agent
py main.py                                 # start the Telegram bot
```

Faculty profile scraping is paused: the host was throttling profile requests, so the stored 314 rows
stay as they are. `--resume` skips profiles already fetched today, so it can be re-run later.

`data/sample_schedules.json` is **demo data only**: the busy blocks are invented. Real rows in the
`office_hours` table always win, and the tool labels the two cases `office_hours` and `sample_data`.
