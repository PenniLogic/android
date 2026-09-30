package com.pennilogic.android.testing

import org.w3c.dom.Document
import org.w3c.dom.Element
import java.io.File
import javax.xml.XMLConstants
import javax.xml.parsers.DocumentBuilderFactory

/**
 * Locates repository files from a unit test whether Gradle runs it from the module directory or
 * the repository root, and parses XML without external entities.
 */
object RepositoryFiles {
    const val ANDROID_NS: String = "http://schemas.android.com/apk/res/android"

    /** The repository root: the closest ancestor of the working directory holding `settings.gradle.kts`. */
    val root: File by lazy {
        var directory: File? = File(checkNotNull(System.getProperty("user.dir")) { "user.dir is unset" }).absoluteFile
        while (directory != null && !directory.resolve("settings.gradle.kts").isFile) {
            directory = directory.parentFile
        }
        checkNotNull(directory) { "settings.gradle.kts not found above ${System.getProperty("user.dir")}" }
    }

    val app: File
        get() = root.resolve("app")

    val docs: File
        get() = root.resolve("docs")

    /** A file under the application module; fails loudly when it is missing. */
    fun appFile(relativePath: String): File = existing(app.resolve(relativePath))

    /** A file under the repository root; fails loudly when it is missing. */
    fun rootFile(relativePath: String): File = existing(root.resolve(relativePath))

    /**
     * The merged manifest of the variant under test, handed over by `app/build.gradle.kts` as the
     * `pennilogic.mergedManifest` system property so the test sees exactly what AGP packaged.
     */
    fun mergedManifest(): File {
        val path =
            checkNotNull(System.getProperty("pennilogic.mergedManifest")) {
                "pennilogic.mergedManifest is not set; app/build.gradle.kts must hand the merged manifest to unit tests"
            }
        return existing(File(path))
    }

    /** Every Kotlin source file of the main source set. */
    fun mainKotlinSources(): List<File> =
        app
            .resolve("src/main/kotlin")
            .walkTopDown()
            .filter { it.isFile && it.extension == "kt" }
            .sortedBy { it.path }
            .toList()

    fun parseXml(file: File): Document {
        val factory =
            DocumentBuilderFactory.newInstance().apply {
                isNamespaceAware = true
                setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true)
                setFeature("http://apache.org/xml/features/disallow-doctype-decl", true)
                isXIncludeAware = false
                isExpandEntityReferences = false
            }
        return factory.newDocumentBuilder().parse(file)
    }

    /** All elements with [tag] in document order. */
    fun Document.elements(tag: String): List<Element> {
        val nodes = getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }

    /** All elements with [tag] below this element in document order. */
    fun Element.elements(tag: String): List<Element> {
        val nodes = getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }

    /** The `android:` attribute, or null when absent. */
    fun Element.androidAttribute(name: String): String? =
        getAttributeNS(ANDROID_NS, name).takeIf { hasAttributeNS(ANDROID_NS, name) }

    private fun existing(file: File): File {
        check(file.isFile) { "expected file is missing: ${file.path}" }
        return file
    }
}
