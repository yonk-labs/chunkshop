# Can You Speed Up Embeddings by Removing Filler Words and Still Keep Accuracy?

Short version, because I respect your time: yes, mostly. You can make embedding about 25% cheaper by stripping the filler words out of your documents before you embed them, and it costs you somewhere between one and two percent of retrieval accuracy. Flat. Across every model I tried.

The long version is more fun, because to get to that number I had to be wrong in public about four separate things first. Let me walk you through it, because the *way* I was wrong is more useful than the answer.

## The dumb idea that works

Embedding models charge you by the amount of text you push through them — compute time if you run them yourself, actual dollars if you call OpenAI. So here's the idea: feed them less. Not fewer chunks. Less text *per* chunk. "The cat sat on the mat" becomes "cat sat mat." Talk like a caveman, embed the caveman, pay for fewer tokens.

chunkshop already had the parts. There's a little reducer called `caveman` that drops stopwords and punctuation, and every chunk carries two text fields: the raw `original_content` you keep for display and grep, and the `embedded_content` that actually goes to the model. So you caveman the thing you embed and leave the real text untouched for humans. No data loss. Just a smaller payload hitting the GPU.

On legal documents, caveman shrank the embedded text by about 18% of its characters and a third of its words. That turned into roughly 27% faster embedding on my box. The cost side was never in question. The question was always: what does it do to search?

## Where I started lying to myself

I had a little test set. Twelve hand-written questions against 772 Supreme Court documents, each one tagged with the answer doc that should come back first. I ran caveman against it, and the results were *fascinating*. Different embedding models reacted completely differently. One lost 14% of its accuracy. One barely flinched at 2%. One (an old 384-dimension workhorse) actually got *better*.

This is catnip for a data nerd. I started building theories. My first one was beautiful: bigger, roomier models have the representational headroom to absorb the weird caveman grammar, so they lose less; small squeezed models feel it more. The numbers lined up across six models. I wrote it up. I was proud of it.

Then I ran a seventh model, bge-large, the biggest full-precision BGE in the lineup, fully expecting it to shrug caveman off. It lost 13%. More than the medium model. So much for headroom.

Fine, new theory: quantization. int8 models throw away precision, caveman throws away words, the two compound. BGE-small backed me up beautifully — int8 lost almost double what the full-precision version did. Clean. Then I ran the same comparison on BGE-base and it came out backwards: the int8 version lost *less*. Two controlled tests, opposite answers.

At this point a smarter voice in the room (not mine) asked the three questions I should've asked first. Is this just noise? Are you even getting the same chunks back, or are you watching the LLM phrase its answer differently? And why are you trusting twelve questions?

## The part where the benchmark admits it was lying

That last question is the whole ballgame, and here's the math I'd been ignoring. With twelve questions, "accuracy at rank 1" can only move in steps of one-twelfth — 8.3 points at a time. So a model showing −13% versus another at −5% isn't a tale of two architectures. It's one or two questions landing differently. I wasn't measuring model behavior. I was measuring a coin landing on its edge twelve times and reading tea leaves in the pattern.

Two tells I'd waved off suddenly looked damning. My 768-dimension model and my 1024-dimension model produced *identical* recall numbers — the metric literally couldn't tell them apart. And the exact same bge-large, served from two different machines, gave me −13% one way and −9% the other. When the serving setup moves your "finding" by four points, you don't have a finding.

So I threw out my homemade test and used real ones. BEIR, the standard retrieval benchmark suite. SciFact, 300 questions about scientific claims with real relevance labels. NFCorpus, 323 medical queries with graded judgments. Hundreds of questions, third-party gold, scored with NDCG, no LLM anywhere in the loop. Here's what caveman actually costs:

| Benchmark | Model | Accuracy change |
|---|---|---|
| SciFact | BGE-small fp32 | −2.3% |
| SciFact | BGE-small int8 | −1.6% |
| SciFact | BGE-base int8 | +0.2% |
| NFCorpus | BGE-small fp32 | −1.5% |
| NFCorpus | BGE-small int8 | −0.5% |

Six hundred-plus questions, two different domains, full-precision and quantized, and the whole thing collapses to a band between +0.2% and −2.3%. Averaging about one percent. The −14%, the +6%, the headroom theory, the quantization theory — all of it was the twelve-question coin flip. None of it survived contact with a real benchmark.

I also ran the question my reviewer actually asked: not "is the answer right," but "do the same chunks come back?" Across every model, caveman retrieval returned about 72% of the same top-10 chunks as the raw version. So caveman reshuffles roughly a quarter of what you retrieve — but the swapped-in chunks are nearly as relevant as the ones they replaced, which is exactly why the accuracy score barely twitches. It changes *which* documents you get, not *how good* they are.

## So what's the actual trade?

Here's the deal, and it's refreshingly boring now that the drama's gone: **about 25% cheaper embedding for about 1-2% lower retrieval accuracy, and you don't have to care which model you're on.**

That 25% wears three different outfits depending on what's pinching you:

If you're on an idle box running a local model, honestly, skip it. You can get the same speed for free by turning up `embedder.threads` and using the cores you already paid for, at zero accuracy cost. (Quick warning on that, since I got it wrong too: threads only help when cores are *idle*. I simulated a busy server with eight jobs fighting over 24 cores, and cranking threads made aggregate throughput 23% *worse*. They just thrash. Caveman, doing less actual work, made it 20% *better*. So on a saturated box the math flips.)

If you're on that busy multi-tenant server, caveman is a real lever — threads have stopped helping you and reducing the work is the only thing left short of buying hardware. And if you're calling a paid per-token embedder, it's the easiest yes on the board: an 18-to-34% smaller payload is a smaller invoice on every ingest, forever, for one or two points of accuracy you'll struggle to even notice in hybrid search (where your keyword leg never sees the reduced text anyway).

## The thing I actually want you to take away

It isn't the caveman trick. It's that I had a clean, confident, model-by-model story — with a *table* — and it was complete garbage, because twelve questions will lie to you with a perfectly straight face. They'll even hand you a plausible mechanism to explain the noise, and you'll write it down, because a pattern that confirms how smart you are is the easiest thing in the world to believe.

The fix wasn't a better theory. It was three hundred questions instead of twelve, and labels somebody else wrote. That's the unglamorous heart of this whole job: the model is almost never the hard part. The hard part is being honest about whether you actually measured the thing you think you measured.

So go ahead — caveman your embeddings, pocket the 25%, lose your two percent. But before you trust *any* benchmark, mine included, count the questions. If it's twelve, it's a vibe, not a result. Run it against a few hundred and tell me what you get. I'll be over here, quietly deleting the headroom theory I was so proud of.
