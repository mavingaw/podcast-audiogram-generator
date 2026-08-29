# The local language model

Clip suggestions are ranked twice. The heuristics in `clipfinder.py` measure
*form* — length, punctuation, pace, filler, audio energy — and they are reliable
about it. What they cannot do is tell whether a passage is worth watching. Two
stretches of speech can score identically on every signal while one is the best
thing in the episode and the other is the host explaining where to find the show
notes.

A local model closes that gap by reading the shortlist.

## What runs

**Qwen2.5-7B-Instruct, Q4_K_M** (~4.4 GB), through llama.cpp in-process.

Chosen for this job, not in general. The task is to read a paragraph of
conversational speech and return strict JSON; at this size, instruction-following
is what decides whether the output parses at all, and a bigger model that
editorialises is worthless here. Q4_K_M is the usual quality knee, and it leaves
room on a 16 GB card for Whisper alongside it.

Override with `PAS_LLM_MODEL` and `PAS_LLM_MODEL_URL`. Nothing in the code is
specific to Qwen beyond the chat template, which llama.cpp reads from the GGUF.

## How it is wired

- **Off by default.** `PAS_LLM=true` turns it on.
- **The weights are not in the image.** Five gigabytes per deploy is not
  acceptable. They are fetched once into the models volume, beside Whisper's.
- **In-process, not a sidecar.** No second container, no port, nothing else to
  be down.
- **Absence is a supported state.** No wheel, no weights, no GPU, no network —
  each is normal, and the heuristic ranking is always the answer. The model only
  ever re-orders it.
- **It reads a shortlist, not an episode.** The heuristic pass exists so the
  model sees a dozen candidates rather than hundreds. An episode costs a couple
  of seconds.
- **Heuristics keep weight.** `LLM_WEIGHT` caps how far the model can move a
  candidate, so a well-formed clip cannot be buried by an enthusiastic rating.

## It selects; it never writes

The first version asked the model for a title, and it produced things like
"Home Tour" and "IT to Care". Good summaries — and words the speaker never said.
A title is *content*: it goes on the post, and sometimes into the frame. Putting
invented phrasing into a guest's mouth is not something a tool should do
quietly, however good the phrasing is.

So the excerpt is now presented as numbered lines and the model chooses which
line is the strongest opening. That number is resolved back to the line, and the
result is checked against the clip text before it is used — a headline that does
not appear verbatim is discarded and the heuristic title stands. The prompt says
so explicitly, but the check is what enforces it, because a prompt is a request
and an invariant is a guarantee. `test_every_title_is_speech_from_its_own_clip`
holds the line.

Titles are shortened by cutting the speaker's sentence short, never by rewording
it.

The `reason` is different: it is the model's assessment of the clip, shown in
the UI as an amber tag beside the measured facts, and never used as content. It
is an opinion, it can be wrong, and it is labelled as one.

Each rated clip returns `hook`, `standalone` and `interest` out of ten, that
reason, and the selected line — so a suggestion can be argued with.

## Building it: two things that bit

**The published CUDA wheels assume AVX-512.** This host is a Threadripper 2990WX
(Zen+), which has AVX2 but not AVX-512. The wheel imports cleanly, reports CUDA
support, and then dies with `SIGILL` the moment it touches a tensor — taking the
API process down with it, because an illegal instruction is not catchable. The
image now compiles llama.cpp in a `devel` stage with `GGML_NATIVE=off` and
`GGML_AVX512=off`, which runs on any x86-64 with AVX2.

**Import failure is not always `ImportError`.** The CUDA build raises
`RuntimeError` when `libcuda.so.1` is missing, which is exactly what happens on a
host with no NVIDIA driver. Catching only `ImportError` let that escape into the
request. `_runtime_importable()` catches everything.

## Choosing a card

`PAS_LLM_GPU` selects the device. **llama.cpp and `nvidia-smi` do not always
agree on ordering** — on this host `nvidia-smi` lists the Quadro RTX 5000 first
while llama.cpp enumerates the 4090 as device 0. Check llama.cpp's own listing,
which is printed at load.

With two cards the sensible split is the model on one and Whisper on the other,
so they never contend for VRAM.

## What it costs

About 4.4 GB of VRAM while resident, and near-zero when idle. It stays loaded
between requests because loading is the expensive part, and it is warmed on a
daemon thread at start-up — otherwise the first person to ask for suggestions
waits five seconds and everyone after them waits two. `unload()` releases it.

## Where this goes next

The model currently rates candidates the heuristics found. The more interesting
version scores against *criteria you set* — "more contrarian takes", "fewer
questions" — which is the thing the build spec calls out as Headliner's weakest
area. The scoring prompt is one string; that is a small change, not a rewrite.
