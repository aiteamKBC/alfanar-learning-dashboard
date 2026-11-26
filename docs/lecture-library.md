# Lecture library

`/lectures` is an authenticated, additive library. It does not change the existing
LMS, historical attendance or approved dashboard KPI tables. Its three models live
in the independent `lecture_library` app/database. Follow [database isolation and
deployment](postgresql-storage.md) to prepare PostgreSQL, then import a privately
collected evidence snapshot:

```powershell
.\.venv\Scripts\python backend/manage.py import_lecture_library --source data/lecture-evidence/source-20261109.json
```

Keep snapshots, transcripts, Microsoft identifiers and the database out of Git and
web roots. The full import is explicit. Future incremental updates have a separate
opt-in scheduler described in [automatic library updates](lecture-library-sync.md).
The SQLite course/learner registry is read by stable LMS source IDs at request time;
no registry rows, accounts or enrollments are copied into PostgreSQL. A library
import uses a PostgreSQL transaction and cannot cascade-delete SQLite evidence.

Each lecture shows the current directory learners enrolled in its module. Source
emails must resolve unambiguously through the current learner/identity registry.
Raw meeting names, staff and unmatched participants are never returned as roster
rows. The API rechecks membership and identity matches on every request, including
after a later LMS sync. Missing attendance is not an inferred absence.

Report aliases for the exact same meeting and actual time window are combined;
attendance intervals are unioned across repeat joins and verified email aliases.
The library presents one entry per module and Cairo calendar day, including
separate calls, short recording fragments and non-overlapping parts of that day.
Original recording/transcript references remain preserved in chronological order;
the recording display rule below hides recordings shorter than ten minutes.
This presentation grouping does not establish that separate calls are the same
meeting, and never authorizes cross-call speaker attribution. Different modules
remain separate. Unconfirmed project archive files are grouped only with other
unconfirmed files on that date, without assigning a module or learners. Undated
records remain separate because their calendar days are unknown. Previously verified
historical timing is not replaced by shorter live evidence. Historical report
fragments attach only when an actual interval for the same verified learner overlaps
that lecture's recorded timing. Otherwise a new report window needs five enrolled
learners with at least 30 minutes of verified presence; smaller sessions are held.
These library rules do not change official lecture denominators elsewhere.
Attendance and transcript speech intervals are unioned before daily totals are
saved, excluding gaps and overlapping duplicates. Archived first/last times are
treated as envelopes, not continuous presence, unless the archived duration fills
the envelope. Overlapping incomplete archival claims retain a conservative total;
disjoint claims are added. Old fragment URLs resolve to their consolidated day,
including authenticated resource links when that same source file remains present.

Media can also attach to an existing lecture without a complete attendance report:
the source series must have an approved course binding, and at least half the
file's actual time window (including 60 seconds) must overlap recorded cohort
presence for exactly one existing lecture. A file title or matching date alone
never suffices. This fallback does not create approved lectures or learner observations.
Every file in an approved source series remains visible even when no attendance
window qualifies: it joins that module's daily entry or becomes a separately
labelled `Recording session` when no other entry exists that day. This
includes short recordings and staff-only calls without changing delivered-lecture
KPIs. Multiple parts of the same call share that session. Files whose content
cannot be accessed remain listed with playback disabled.

Original OneDrive items explicitly titled `PCP Alfanar Group` may be imported as
project archive recording sessions with a null course. They are labelled
`module unconfirmed`, receive no attributed learners or attendance, and do not
attach to historical lectures by date. Archive dates use the original UTC recording
filename and durations use the source video's metadata. The private audit retains
the provenance and compares source byte samples and file size to screen for
duplicates; this is not a full-file checksum. Individual support recordings are
outside this lecture archive. OneDrive streaming uses exact drive/item IDs.
Unretrieved media is described as not yet linked, rather than declared nonexistent.

Speaking time is estimated from the transcript when an exact speaker label matches
one participant in that same report/window, that participant is already identified
by an unambiguous registered email, and the utterance fits their recorded presence
(two seconds of timestamp tolerance). Outsiders participate in name-ambiguity
detection, but never become roster rows. Similar names, cross-meeting matches and
utterances outside recorded presence do not qualify. This attribution is labelled
`meeting_name`, not an identity-verified speaker binding. Names never create learner
identities. Missing or ambiguous speech remains unavailable rather than zero.

An optional `--speaker-bindings` JSON file can provide stronger owner-verified identity evidence: an array of `meeting_id`,
`call_id`, `speaker_name`, `identity_id`, `verified: true` and an `evidence` reference.
The importer validates the identity against cohort-matched attendance and unions
the speaker's transcript intervals. It marks a result `verified_identity` only if
verified evidence covers every contributing speech interval. The API revalidates
the emails behind speech after registry changes, independently of attendance.
Missing camera/microphone events also remain
unavailable rather than false.

Recordings stream through authenticated, range-aware API endpoints. Microsoft tokens
and signed redirect URLs are never sent to the frontend. Redirects are restricted
to Microsoft storage hosts, without forwarding Graph authorization to those hosts.
Readable source transcripts download as text or VTT under the same dashboard access
rules. Full source media can include the meeting's other participants; they do not
become directory rows or receive inferred learner identities.

Advanced Research Methods remains a live, read-only LMS view. Its completed
occurrences read their visible recording/transcript references from
`curriculum.live_session_artifacts`, scoped to that module and occurrence. Media
uses the exact meeting and organizer in each artifact's metadata; a recurring
series ID is not interchangeable with the recorded meeting ID. Downloads and
range-aware playback use the existing authenticated proxy. Transcripts use the
same metadata-to-text/VTT parser, with no Graph URLs or credentials in the browser.
No LMS or dashboard/library rows are created, copied, changed or cached. Missing
or hidden artifact bindings remain unavailable. New LMS artifacts appear on the
next page read without adding this module to the four-course discovery policy.
LMS attendance statuses/durations and the current speaking-time projection stay
unchanged; this media connection does not infer learner speech from labels.

The live LMS `completed` flag alone does not finalize attendance: a short ended
call can set it while the scheduled lecture is still running. ARM publication
waits until the later of the scheduled end (in the session timezone) and actual
UTC end, plus a 15-minute reporting delay by default. Configure
`LMS_ATTENDANCE_PUBLICATION_DELAY_MINUTES` to adjust this read-only LMS display
delay (1–1440 minutes). Every current cohort attendance
row must have a non-null calculation timestamp at/after that end. Early fragments,
ongoing/future sessions and stale/partially refreshed attendance stay out of lecture
lists, detail/media routes, learner attendance and dashboard denominators. Once
eligible, the LMS statuses/durations are displayed as stored. These are read-only
publication checks; no source attendance, schedules or library rows are rewritten.

Recording display hides every clip with a verified duration shorter than 10
minutes, including lectures whose recordings are all short. Parts of at least
10 minutes and unknown durations remain visible. This applies to existing and
future library and live LMS lectures. Transcript parts and all attendance/speech
evidence remain untouched. List counts and detail buttons use the same selection.

LMS media duration is read from each artifact's exact start/end. Legacy library
durations use a minimal private index of exact provider/drive/organizer/meeting/file
IDs, with no titles, learner records, signed URLs or transcript text. Full imports
and hourly library sync atomically refresh that index. Unknown/missing metadata
keeps recordings visible rather than guessing. No database migration is needed.
For an existing installation, bootstrap it from the current approved source:

```sh
python backend/manage.py index_lecture_recordings --source /PRIVATE/current-evidence.json
```

The default output is the ignored `data/lecture-recording-durations.json`; configure
`LECTURE_RECORDING_DURATIONS_FILE` to use another private path readable by the web
process and writable by the sync job. Source files and original recordings are
never edited or deleted. Index replacement is picked up on the next request.

Validation: `python backend/manage.py test analytics.tests.test_lecture_library`
and `npm --prefix frontend run build`.
