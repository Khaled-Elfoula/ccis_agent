# Demo script

Twelve messages that show what the agent does and where each answer comes from.

Three doctors carry the demo. Each has a scraped email and real published office hours in the database:

| Doctor | Username | Department | Office | Email | Office hours |
|---|---|---|---|---|---|
| Dr. Saad Abdullah AlAhmadi (أ.د. سعد عبدالله الأحمدي) | `salahmadi` | CS | 31 / 2179 | salahmadi@ksu.edu.sa | Sun–Tue 16:00–18:00, Wed–Thu 15:00–17:00 |
| Dr. Duaa H. AlSaeed (أ.د. دعاء حمد السعيد) | `dalsaeed` | IT | 06 / T035 | dalsaeed@ksu.edu.sa | Sun–Mon 12:00–13:00, Tue–Thu 12:00–14:00 |
| Dr. Hessah A. Alraqibah (حصة عبدالله الرقيبة) | `halraqibah` | CS | 06 / T136 | halraqibah@ksu.edu.sa | Sun 09:00–10:00, Tue 12:00–13:00, Wed 10:00–12:00, Thu 09:00–10:00 |

Dr. Sami Alwakeel (`swakeel`, CE, room 2206, swakeel@ksu.edu.sa) has **no** published office hours, so the
demo can show what the agent says in that case.

Name matching handles the short English form: "Dr. Saad Al-Ahmadi" resolves to the same person as the full
name. A name that only partly matches comes back as **candidates, not confirmed**, and the agent asks
"did you mean X?" instead of guessing.

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
   `faculty.ksu.edu.sa/ar/salahmadi`. Nothing invented.

5. **"And Dr. Saad Abdallah AlAhmadi?"** (misspelled)
   The same person. Name matching tolerates the missing "u" and returns the same contact details.

6. **"When is Dr. Saad AlAhmadi free on Sunday?"**
   `get_office_hours` returns Sunday 16:00–18:00 with the profile link and the date it was read. The answer
   says these are his **published office hours** and that nothing was booked.

7. **"What about Dr. Duaa AlSaeed on Tuesday?"**
   `get_office_hours` returns Tuesday 12:00–14:00, again as published hours, with her profile link.

8. **"When is Dr. Sami Alwakeel free?"**
   `get_office_hours` returns `no_office_hours: true` with swakeel@ksu.edu.sa, room 2206 and his profile
   link. The answer says he has no published office hours and suggests emailing him. It does not estimate.

9. **"When are the final exams for the first semester?"**
   `get_calendar` returns two separate rows: بداية الاختبارات النهائية on 2026-12-13 and نهاية الاختبارات
   النهائية on 2026-12-26, both with `dar.ksu.edu.sa/ar/current`. The answer gives both dates and says the
   calendar is for 2026-2027.

10. **"What is the last day to drop a course?"**
    `get_calendar` answers from the academic-movements page (`dar.ksu.edu.sa/ar/academictable`), not the
    calendar page: الحذف والإضافة for the first semester ran 2026-08-18 to 2026-08-22 and comes back `past`;
    for the second semester it runs 2027-01-04 to 2027-01-09 and comes back `upcoming`, with the days left
    computed by the tool. The answer gives the status and the dates and does not count the days itself.

11. **"I want to withdraw from a course after the deadline."**
    `get_calendar` returns two different movements: a course withdrawal (الاعتذار عن مقرر دراسي / فصل دراسي,
    open now, ending 2026-11-28) and a withdrawal from the university (الانسحاب من الجامعة, open now, ending
    2026-12-10). The answer says which one it is answering, then hands off: the reason, the College Office
    for Educational and Academic Affairs (`ccis.ksu.edu.sa/ar/student-affairs-agency-mail`), and a ready
    email template with `[Your name]` and `[Your ID]`. That office has no published email, so the answer says
    the email was not found and gives the page link.

12. **"What's the cafeteria menu?"**
    Out of scope. The agent declines and says what it can help with. No tool call, no invented answer.

## Commands

```bash
py -m scrapers.run --all                   # every source below plus row counts
py -m scrapers.run --calendar              # only the academic calendar
py -m scrapers.run --deadlines             # only the academic-movements table
py -m scrapers.run --catalog               # only the SE course catalog
py -m scrapers.run --programs              # only the three degree-program pages
py -m scrapers.run --faculty --resume      # faculty lists and profiles (skips downloaded profiles)
py scripts/report_faculty.py               # data report: counts, coverage, contacts
py scripts/prepare_deploy.py               # checkpoint the DB, show its size, print the git commands
py -m pytest -q                            # the whole test suite
py scripts/demo_check.py                   # run the twelve messages through the real agent
py main.py                                 # start the Telegram bot
```

`--all` refreshes the college pages, the five faculty lists, the calendar, the academic-movements table and
the course catalog, then prints the row count of each table. It does **not** fetch profiles; use `--faculty`
for those. Faculty profile scraping is paused: the host was throttling profile requests, so the stored 314
rows stay as they are.

The database is refreshed by hand and then committed. The bot never scrapes on a schedule and never while a
user waits.
