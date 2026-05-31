# Can You Speed Up Embeddings by Removing Filler Words and Still Keep Accuracy?

I spent an afternoon trying to make my documents dumber on purpose.

The idea is stupid in the way that good experiments usually are. Embedding models charge you — in time if you run them locally, in actual dollars if you call OpenAI — by how much text you feed them. So what if you fed them less? Not fewer chunks. Less *text per chunk*. Rip out the filler. "The cat sat on the mat" becomes "cat sat mat." Talk like a caveman, embed the caveman, save the compute.

chunkshop already had the part to do it. There's a little reducer in there called `caveman` that strips stopwords and punctuation and hands back the meaning-bearing words. And chunks carry two text fields — the raw `original_content` you keep for display and grep, and the `embedded_content` that actually goes to the model. So you can caveman the thing you embed while keeping the real text intact for humans. No data loss. Just a smaller payload hitting the GPU.

I figured it'd be a clean trade: cheaper embeddings, slightly worse search. The question was *how much* worse. Turns out I was wrong about almost every part of that sentence, including the part where I thought I'd measured it correctly.

## The disaster that wasn't

First run, SCOTUS corpus — 772 legal documents, 12 hand-written gold questions where I know exactly which doc should come back first. Baseline recall@1 of 0.67. Caveman version: 0.33. Cut in half. Mean reciprocal rank dropped a third. One gold document fell completely out of the top 50.

I almost wrote that up. "Caveman reduction destroys semantic search, don't do it, the end." Clean story, good headline, would've gotten some nods on Hacker News.

Here's the deal, though: it was a measurement bug, and it was mine. The default chunker (`hierarchy`) does this clever thing where it prepends the section heading to the embedded text — that heading is free framing context, and it's a big part of why that chunker wins on prose. My caveman path was running through a different code route that stripped the body filler *and* quietly dropped the heading. So I wasn't measuring "what does removing filler do." I was measuring "what does removing filler AND deleting the most important sentence do." Two changes, one number, garbage conclusion.

This is the oldest trap in benchmarking and I walked right into it after twenty years of telling other people not to. Change one variable. Just one. When you're A/B testing a text transform, the chunk framing has to be identical on both sides or you're measuring the framing. (Yes, I know. I know.)

So I rebuilt it. Same heading-prefixed text on both sides. The only difference between A and B is now the filler words, nothing else. And while I was in there, I figured I'd answer the better question anyway: does the answer change depending on which embedding model you use?

## What actually happened

Smaller first. Caveman shrank the embedded text about 18% by characters and roughly 34% by token count, which translated to ingestion embedding running about 27% faster. That part held up. The text really is cheaper to embed, and the speedup is real money if you're paying per token.

Now the accuracy, measured properly, across six models including OpenAI's `text-embedding-3-small`:

| Model | Baseline MRR | Caveman (raw query) | Caveman (caveman query) |
|---|---|---|---|
| nomic-embed-v1.5-Q (768) | 0.896 | −2% | −2% |
| bge-large-en-v1.5 fp32 (1024) | 0.842 | −13% | −12% |
| BGE-base int8 (768) | 0.806 | −5% | −3% |
| OpenAI text-embedding-3-small (1536) | 0.792 | −7% | **−2%** |
| BGE-small fp32 (384) | 0.765 | −8% | −8% |
| BGE-small int8 (384) | 0.766 | −14% | −15% |
| all-MiniLM-L6-v2 (384) | 0.667 | **+6%** | **+10%** |

So it's not a disaster. It's a nibble. Mostly a 2-to-8% hit, worse on one model, and — this is the part I didn't see coming — *better* on one. all-MiniLM-L6-v2, the old reliable 384-dimension workhorse, got more accurate when I took the filler away. My read: it's a smaller, weaker model, and the stopwords were noise it couldn't see past. Strip the noise, the signal gets louder.

A few things fell out of this that are worth your Tuesday:

**The sign flips by model.** This was the actual question, and the answer is yes. A weak model can *gain* from filler removal while a strong one loses a little. There is no universal "caveman is good" or "caveman is bad." There's only "caveman is good or bad *for this model on this corpus*," which is the most data-engineering sentence I will write all week.

**Quantization makes it worse.** BGE-small in int8 lost 14%. The same model in full fp32 lost 8%, about half that. Int8 vectors are already coarse, and feeding them weird caveman-grammar that the model never saw in training pushes them further off. If you're going to reduce text, don't also crush the model to int8 and expect it to shrug it off.

**Match the query to the index.** OpenAI lost 7% when I caveman'd the documents but searched with normal questions. When I caveman'd the *query* too — so both sides speak the same broken grammar — the loss dropped to 2%. If your index talks like a caveman, your queries should too. (BGE-small was the lone exception that got grumpier when I did this, because of course there's an exception.)

**I tried to find a rule by model size, and the data shot it down.** Here's the part where I get to be wrong in public again, which is becoming a theme. Looking at the first batch, I was *sure* I saw it: bigger, roomier models shrug caveman off, small squeezed ones feel it. Clean story. Then I ran bge-large — 1024 dimensions, full fp32, the biggest BGE in the lineup — fully expecting it to barely flinch. It lost 13%. Worse than BGE-base. Worse than BGE-small in fp32. So much for "big models have room to spare."

So there is no tidy law by size or dimension. nomic at 768 loses 2%; bge-large at 1024 loses 13%; they're both big and they're nowhere near each other. The only pattern that actually held up is the controlled one — same model, change only the quantization: BGE-small went from −8% in fp32 to −14% in int8. That's a real, repeatable effect because nothing else moved. Everything *across* models is model-specific, and with 12 questions a −13% versus −5% gap is one or two queries flipping, which is to say partly noise. Which leaves exactly one honest instruction: measure it on your model and your corpus, because I cannot give you a shortcut that survives contact with the next model you try. I keep trying. It keeps not working.

## So should you do it?

Look, this is my take, and 12 gold questions is a small sample, so hold it loosely. My first instinct was to wave people off: just turn up `embedder.threads` and get the speed for free. On my 24-core box, going from 4 threads to 12 made embedding about 1.5x faster at zero recall cost, so why pay accuracy for it?

Because "free" was me getting lazy again. Threads don't make the work smaller. They spread the same matrix math across more cores. That's a fantastic deal when the cores are sitting idle, which is exactly the situation a benchmark on my laptop creates and a production server almost never does. The second you have a busy box, those cores aren't free. They're spoken for.

So I simulated a busy server: eight embedding jobs running at once on 24 cores, and measured total throughput across all of them. Cranking each job to 12 threads (eight jobs fighting over 24 cores, asking for 96) didn't speed things up. It dropped aggregate throughput **23.5%**. The threads spent their time context-switching instead of working. Caveman, running at modest threads, *raised* throughput **20.6%** in the same crowded conditions — because it's less actual work, and less work scales when cores don't.

That reframes the whole thing. It's not "free speed vs. paid accuracy." It's a real trade, and which side wins depends on your box:

If you're on an **idle or single-tenant** machine with a local model, you're right to skip caveman. You've got spare cores; spend them on threads and keep your accuracy.

If you're running a **busy, multi-tenant** server (the hundreds-of-users case), threads stop saving you. They start costing you. Now caveman's 20% throughput bump for a model-dependent 2-to-15% recall hit is an actual lever, maybe the only one you've got short of buying more hardware.

If you're calling a **paid, per-token** embedder like OpenAI, it's the easiest yes on the board. An 18% smaller payload is an 18% smaller invoice, every ingest, forever, and the accuracy cost was 2%, as long as you caveman the query too. Two percent MRR for a standing discount on your embedding bill is a trade plenty of people would take.

And if you're stuck on a **small or older** model for latency reasons, run the test. Filler removal might quietly make your search *better*, which is a nice problem to have.

The meta-lesson is the one I keep relearning and keep almost forgetting: in AI work the hard problem is never the model, it's the data engineering around it. The same blunt little stopword-stripper helped one model, barely touched another, and clipped a third. The difference between "this destroys recall" and "this costs 2%" was a heading I forgot to hold constant. The model was never the story. The pipeline was.

Want to break my numbers? The harness chunks once and re-embeds per model, so it's cheap to point at your own corpus and your own gold set. Run it on something bigger than 12 questions and tell me where I'm wrong. I'd genuinely like to know — especially if your weak model gets faster *and* better, because then we're all going to feel a little silly about how much we've been paying to embed the word "the."
