# Changelog

## 2026-03-06 (latest)

### Chapters
- Auto-generate topic chapters for any transcript using GPT-5
- Chapters generated automatically on new transcriptions alongside summary
- "Generate Chapters" button for existing transcripts
- Clickable chapter nav (table of contents) with timestamps, shown below the summary
- Chapter headers rendered inline in the utterance list as dividers
- Clicking a chapter nav item seeks the audio player and scrolls to that section

### Collapsible action items
- Action items section now has a toggle header showing count (e.g. "Action Items (4)")
- Click to collapse/expand — resets to open when switching transcripts

### Playback speed
- Added 1.75x speed option between 1.5x and 2x

---

## 2026-03-06

### iOS clipboard & mobile UI fixes
- **Fixed clipboard copy on iPhone**: Replaced textarea fallback with contentEditable div positioned at `top: 0, left: 0` for iOS Safari compatibility
- **Mobile button layout**: Reduced "Copy for ChatGPT" button width on mobile, kept all action buttons on one line
- **Improved button spacing**: Reduced padding on mobile (20px → 12px for copy, 14px → 10px for export buttons)
- **Fixed comment badge cutoff**: Added top padding to action bar so notification badge displays fully visible
- **Page refresh persistence**: Transcript view now persists across page reloads using localStorage

---

## 2026-03-05

### Comments
- Add comments on any utterance — click the speech bubble icon to leave a note
- Collapsible comments sidebar lists all comments, click to jump to that utterance
- Comment count badge on the toggle button
- Commented utterances highlighted with blue right border

### Smarter action items
- GPT only generates action items when genuinely warranted (no more filler tasks for casual meetings)
- Personal action items: set your name in Settings, GPT tags items assigned to you
- "Your Action Items" section shown separately above other items

### Settings page
- New settings view (gear icon on home screen)
- Profile configuration: name + role used for personalized action items
- Usage & Costs moved into settings as a collapsible section

### Cost alert on upload
- Toast notification after transcription shows duration and estimated cost

### Utterance merging on load
- Existing transcripts now get same-speaker merging applied when opened (not just new ones)

---

## 2026-03-04

- Fix clipboard copy on iPhone Safari (fallback for HTTP/non-secure contexts)
- Improve utterance merging: consecutive same-speaker utterances within 15s are now combined (was only merging short fragments under 8 words within 5s)
- Make audio player sticky so it follows you when scrolling long transcripts

---

## 2026-02-26

### Multi-file combine & upload queue
- Upload multiple recordings from the same meeting and combine them into one transcript
- Files are stitched together with ffmpeg — audio plays back seamlessly
- File-boundary markers show where each recording starts in the transcript
- "Combined from: file1, file2" note displayed at the top
- New staged upload UI: pick files one at a time, review the list, then tap "Transcribe"
- Works on iPhone — no more instant upload when selecting a file

### Rename transcripts
- Tap the title (or pencil icon) on any transcript to rename it
- New `PATCH /api/transcripts/:id/rename` endpoint

### Code refactor
- Extracted shared transcription logic into `transcription.py` (Deepgram calls, utterance parsing, error handling, file cleanup)
- Both single and multi-file endpoints use shared helpers — no duplicated code
- All utterance iterators (search, export, voiceprints, copy text) handle file-boundary entries

### Fixes
- Transcripts now sorted by `created_at` date instead of file modification time
- iPhone audio playback: handled iOS Safari autoplay restrictions, wait for audio load before seeking
- SRT export skips file-boundary entries and numbers correctly

---

## 2026-02-24

### Action items & voice recognition
- AI-generated action items extracted from transcripts (accept, dismiss, delete)
- Click an action item to highlight the related section in the transcript
- Automatic speaker identification using voice fingerprints (resemblyzer)
- Rename a speaker and their voice embedding is saved for future matching
- GPT-5 integration for summaries and action items
- Modular refactor: split code into `ai.py`, `voiceprints.py`, `storage.py`, `helpers.py`

### Housekeeping
- Hardened `.gitignore` to block all media, logs, and data files
- Added `AGENTS.md` with coding rules for AI dev tools (Claude Code, OpenCode, Aider)

---

## 2026-02-21

### Bookmarks, playback speed & summaries
- Bookmark utterances and filter to show only bookmarked
- Playback speed control: 1x, 1.25x, 1.5x, 2x
- AI summary generation (GPT-5) with manual trigger button
- Short utterance merging: consecutive same-speaker lines under 8 words are combined

### Tagging & live recording
- Tag transcripts with color-coded labels
- Filter transcript list by tag
- "Copy All for ChatGPT" exports all transcripts with a selected tag
- Live audio recording directly in the browser
- Progressive backup downloads every 30 seconds during recording
- Auto-downloads transcript text after recording completes

### Initial release
- Upload audio/video files for transcription (Deepgram Nova-3)
- Automatic speaker diarization
- Click-to-rename speaker labels
- Built-in audio player synced to transcript with click-to-seek
- In-transcript search with match navigation
- Global search across all transcripts
- Export as `.txt` or `.srt`
- "Copy for ChatGPT" formatted export
- Usage & cost tracking dashboard
- Dark mode UI, mobile-friendly
