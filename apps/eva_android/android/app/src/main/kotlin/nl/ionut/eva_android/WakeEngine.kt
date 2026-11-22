package nl.ionut.eva_android

import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.io.File
import java.nio.FloatBuffer

/**
 * The same wake word pipeline as the PC (core/wakeword/features.py), number for number:
 *   last 2 s of 16 kHz audio (int16 values as floats) -> melspectrogram.onnx -> x / 10 + 2
 *   -> 76-frame windows every 8 frames -> embedding_model.onnx (96 values each) -> the last 16
 *   -> eva.onnx (your trained classifier) -> a score from 0 to 1.
 * The phone downloads the three files from your PC, so a retrained model arrives by itself.
 */
class WakeEngine(dir: File) : AutoCloseable {
    private val env = OrtEnvironment.getEnvironment()
    private val opts = OrtSession.SessionOptions().apply {
        setIntraOpNumThreads(1)
        setInterOpNumThreads(1)
    }
    private val mel = env.createSession(File(dir, "melspectrogram.onnx").absolutePath, opts)
    private val emb = env.createSession(File(dir, "embedding_model.onnx").absolutePath, opts)
    private val clf = env.createSession(File(dir, "eva.onnx").absolutePath, opts)
    private val clfInput = clf.inputNames.first()

    /** Score for one 2-second clip (32000 samples). */
    fun score(clip: ShortArray): Float {
        val spec = melspectrogram(clip)                       // [frames][32]
        val starts = ArrayList<Int>()
        var i = 0
        while (i + WINDOW <= spec.size) {
            starts.add(i)
            i += STEP
        }
        if (starts.isEmpty()) return 0f
        val batch = FloatArray(starts.size * WINDOW * MELS)
        var k = 0
        for (s in starts) for (t in 0 until WINDOW) for (m in 0 until MELS) batch[k++] = spec[s + t][m]
        val embeddings: List<FloatArray> = OnnxTensor.createTensor(
            env, FloatBuffer.wrap(batch), longArrayOf(starts.size.toLong(), WINDOW.toLong(), MELS.toLong(), 1L)
        ).use { t ->
            emb.run(mapOf("input_1" to t)).use { r ->
                @Suppress("UNCHECKED_CAST")
                val out = r[0].value as Array<Array<Array<FloatArray>>>     // [n][1][1][96]
                out.map { it[0][0] }
            }
        }
        // the last 16 embeddings, padded with zeros in front when the clip is short (as in training)
        val x = FloatArray(FRAMES * DIM)
        val take = embeddings.takeLast(FRAMES)
        val offset = FRAMES - take.size
        for ((j, e) in take.withIndex()) System.arraycopy(e, 0, x, (offset + j) * DIM, DIM)
        return OnnxTensor.createTensor(env, FloatBuffer.wrap(x), longArrayOf(1L, FRAMES.toLong(), DIM.toLong())).use { t ->
            clf.run(mapOf(clfInput to t)).use { r -> firstFloat(r[0].value) }
        }
    }

    private fun melspectrogram(clip: ShortArray): Array<FloatArray> {
        val x = FloatArray(clip.size) { clip[it].toFloat() }
        return OnnxTensor.createTensor(env, FloatBuffer.wrap(x), longArrayOf(1L, clip.size.toLong())).use { t ->
            mel.run(mapOf("input" to t)).use { r ->
                @Suppress("UNCHECKED_CAST")
                val out = r[0].value as Array<Array<Array<FloatArray>>>     // [1][1][frames][32]
                Array(out[0][0].size) { f -> FloatArray(MELS) { m -> out[0][0][f][m] / 10f + 2f } }
            }
        }
    }

    private fun firstFloat(v: Any?): Float = when (v) {
        is Float -> v
        is FloatArray -> v[0]
        is Array<*> -> firstFloat(v[0])
        else -> 0f
    }

    override fun close() {
        clf.close()
        emb.close()
        mel.close()
        opts.close()
    }

    companion object {
        const val SAMPLE_RATE = 16000
        const val CLIP = 2 * SAMPLE_RATE      // 2 s -> 197 mel frames -> 16 embeddings
        const val WINDOW = 76
        const val STEP = 8
        const val MELS = 32
        const val FRAMES = 16
        const val DIM = 96
    }
}
