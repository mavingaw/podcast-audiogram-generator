# Speaker labelling

Captions can carry who is speaking: each speaker gets a name and a colour, and
burned-in captions tint to match. Assignment is **manual** — you tag a line and
the rest of that speaker's turn follows.

That is a deliberate choice, and this document exists to record why, because
"just add diarization" looks obvious and is not.

## How it works

Two ONNX models, both baked into the image (46 MB together, no PyTorch):

- **pyannote segmentation 3.0** finds where speech starts and stops, and where
  turns change.
- **NeMo TitaNet-small** turns each turn into a speaker embedding.

Those embeddings are clustered, and the clusters become speakers 1..N, numbered
by who talks first. Run through `sherpa-onnx`, which drives both without the
three gigabytes of PyTorch the usual pyannote route needs.

## You are asked how many people are talking

Because guessing is the weak part, and the measurements are unambiguous.

| Setting | Reference recording (4 speakers) | A real single-host episode |
|---|---|---|
| sherpa default, threshold 0.5 | 8 speakers | 4 speakers |
| threshold 0.9 | **4 speakers** | 4 speakers |
| threshold 1.1 | — | **1 speaker** |

There is no threshold that is right for both. Told the number instead, the same
episode splits cleanly and evenly (11 lines against 12); told there is one
speaker, detection is skipped outright rather than allowed to return four.

The number of people in the room is the one thing the person editing definitely
knows. A question with a certain answer beats an estimate that is wrong a third
of the time, so the panel asks. Automatic estimation is still there for when
nobody says, and is honest about being the weaker path.

## Why not something simpler

Before reaching for a model, two dependency-free approaches were built and
measured. Both failed, and the numbers rule them out rather than merely
disappointing:

**Long-term average spectrum.** Mel-band energies per segment, clustered by
cosine distance. It separated synthetic tones beautifully. On real speech with
two clearly different voices: within one speaker 0.012-0.037, between two
speakers 0.010-0.027. No separation at all — averaged over seconds, the spectrum
of speech is dominated by which phonemes were said, not by who said them.

**Fundamental frequency.** The classic discriminator, and it looked promising:
two voices at roughly 140 Hz and 112 Hz. Measured across every segment on a
semitone scale: within one speaker up to 2.44 semitones, between two speakers as
little as 1.22, and a single monologue spanning 6.96. One person's intonation
varies more across an episode than two people differ from each other. Clustering
on it produced four speakers from one person talking.

## What reaches the screen

- Each transcript segment carries a numeric `speaker_id`; the transcript carries
  `speaker_names` for those numbers.
- Each speaker takes a colour from the brand palette in order — baby blue, then
  Champagne Gold, then a violet and a green.
- Burned-in captions tint to the speaker's colour, and word-by-word highlighting
  still works on top: the spoken word takes the highlight colour while the rest
  of the line stays in the speaker's.
- On a plated caption preset the plate already carries the accent, so the plate
  carries attribution instead and the type stays legible.
- **A clip with one speaker renders exactly as it did before this existed** — no
  tint, no prefix. The feature costs nothing when it is not used.

## Corrections stick

Names are set by hand and survive detection being re-run: a correction is not
undone by asking the model to look again. Speech can be reassigned by time range
rather than line by line, because people talk in turns and fixing an hour of
audio one sentence at a time is not something anybody would do twice.
