# Module order and verified dates

The client display order is keyed by stable LMS course IDs in `analytics/course_labels.py`:

1. 83334 — Planning, Controlling and Leading a Project
2. 121642 — Procurement Risk and Contract Management
3. 135945 — Operations and Information Management for Project Managers
4. 154268 — Advanced Project and Logistics Management

Filters, Overview module cards and chart datasets, learner enrollment lists, profile cards and learner-module matrix columns all use this order. Unknown modules follow these four alphabetically. Source titles, course IDs, attendance mappings and KPI calculations are unaffected.

## Date verification

The source review inspected all 15 live Google Sheets LMS worksheets, the older local LMS workbook, all worksheets in the four authoritative attendance workbooks and the previous Teams export. No verified module-level start and end dates were found.

- The LMS has learner registration dates, lesson timestamps and quiz/submission activity times, which are not official course boundaries.
- The attendance matrices and Teams reports have session or meeting dates, which do not establish official module start/end dates.
- The `Start-Date` and `End-Date` columns in the PCP workbook describe a separate programme identified by `Program Name`, not the PCP module itself. They are not used as module dates.

Date labels therefore remain hidden for all four modules. No first/last attendance range, registration date or programme date is presented as an official module date. A future verified module schedule should supply both dates and their provenance before the UI displays `Start: DD MMM YYYY · End: DD MMM YYYY`.

The removed attendance description blocks remain absent from Overview module cards. Detailed attendance explanations remain in their existing information tooltips.
