package com.pennilogic.android.contract

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Document
import org.w3c.dom.Element
import java.io.File
import javax.xml.XMLConstants
import javax.xml.parsers.DocumentBuilderFactory

/**
 * Guards the security defaults declared in the manifest and its network security configuration.
 * Any change to these attributes must be deliberate and reviewed, so it has to update this test.
 */
class ManifestContractTest {
    private val manifest: Document by lazy { parse(sourceFile("src/main/AndroidManifest.xml")) }
    private val networkSecurityConfig: Document by lazy {
        parse(sourceFile("src/main/res/xml/network_security_config.xml"))
    }

    @Test
    fun `cleartext traffic is disabled in the manifest`() {
        assertEquals("false", application().getAttributeNS(ANDROID_NS, "usesCleartextTraffic"))
    }

    @Test
    fun `network security config is referenced and forbids cleartext everywhere`() {
        assertEquals(
            "@xml/network_security_config",
            application().getAttributeNS(ANDROID_NS, "networkSecurityConfig"),
        )

        val baseConfigs = networkSecurityConfig.getElementsByTagName("base-config")
        assertEquals(1, baseConfigs.length)
        assertEquals("false", (baseConfigs.item(0) as Element).getAttribute("cleartextTrafficPermitted"))
        assertEquals(0, networkSecurityConfig.getElementsByTagName("domain-config").length)
        assertEquals(0, networkSecurityConfig.getElementsByTagName("debug-overrides").length)
    }

    @Test
    fun `application data is excluded from backup and transfer`() {
        assertEquals("false", application().getAttributeNS(ANDROID_NS, "allowBackup"))
        assertEquals("@xml/data_extraction_rules", application().getAttributeNS(ANDROID_NS, "dataExtractionRules"))
        assertEquals("@xml/backup_rules", application().getAttributeNS(ANDROID_NS, "fullBackupContent"))

        val extractionRules = parse(sourceFile("src/main/res/xml/data_extraction_rules.xml"))
        for (section in listOf("cloud-backup", "device-transfer")) {
            val nodes = extractionRules.getElementsByTagName(section)
            assertEquals(1, nodes.length)
            assertExcludesEveryDomain(nodes.item(0) as Element)
        }
        val backupRules = parse(sourceFile("src/main/res/xml/backup_rules.xml"))
        assertExcludesEveryDomain(backupRules.documentElement)
    }

    private fun assertExcludesEveryDomain(section: Element) {
        assertEquals(0, section.getElementsByTagName("include").length)
        val excluded =
            (0 until section.getElementsByTagName("exclude").length)
                .map { (section.getElementsByTagName("exclude").item(it) as Element).getAttribute("domain") }
                .toSet()
        assertEquals(setOf("root", "file", "database", "sharedpref", "external"), excluded)
    }

    @Test
    fun `scaffold declares no permissions`() {
        assertEquals(0, manifest.getElementsByTagName("uses-permission").length)
        assertEquals(0, manifest.getElementsByTagName("uses-permission-sdk-23").length)
    }

    @Test
    fun `only the entry activity is exported`() {
        val activities = manifest.getElementsByTagName("activity")
        assertEquals(1, activities.length)
        val entry = activities.item(0) as Element
        assertEquals(".MainActivity", entry.getAttributeNS(ANDROID_NS, "name"))
        assertEquals("true", entry.getAttributeNS(ANDROID_NS, "exported"))
        assertEquals(1, entry.getElementsByTagName("intent-filter").length)
        for (tag in listOf("service", "receiver", "provider")) {
            assertEquals("unexpected <$tag> in scaffold manifest", 0, manifest.getElementsByTagName(tag).length)
        }
    }

    private fun application(): Element {
        val applications = manifest.getElementsByTagName("application")
        assertEquals(1, applications.length)
        return applications.item(0) as Element
    }

    /** Resolves a module source file whether the test runs from the module or the repository root. */
    private fun sourceFile(relativePath: String): File {
        val workingDirectory = File(checkNotNull(System.getProperty("user.dir")) { "user.dir is unset" })
        val candidates = listOf(workingDirectory.resolve(relativePath), workingDirectory.resolve("app/$relativePath"))
        val file = candidates.firstOrNull { it.isFile }
        assertTrue("source file not found in ${candidates.map { it.path }}", file != null)
        return file!!
    }

    private fun parse(file: File): Document {
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

    private companion object {
        const val ANDROID_NS = "http://schemas.android.com/apk/res/android"
    }
}
