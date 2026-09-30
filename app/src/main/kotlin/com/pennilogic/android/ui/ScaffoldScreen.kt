package com.pennilogic.android.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.pennilogic.android.R
import com.pennilogic.android.platform.ui.RootSurface
import com.pennilogic.android.ui.theme.PenniLogicTheme

/** Semantics test tags of the scaffold screen. */
object ScaffoldScreenTags {
    const val HEADING: String = "pennilogic.scaffold_heading"
}

/**
 * The only screen of the scaffold: build identity and configuration status, read-only. It is
 * content for a [RootSurface]: insets and the large-screen width policy are applied by the root,
 * never here.
 */
@Composable
fun ScaffoldScreen(
    state: ScaffoldScreenState,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier =
            modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(vertical = 32.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = stringResource(R.string.app_name),
            style = MaterialTheme.typography.headlineMedium,
            modifier = Modifier.semantics { heading() }.testTag(ScaffoldScreenTags.HEADING),
        )
        Text(
            text = stringResource(R.string.scaffold_subtitle),
            style = MaterialTheme.typography.bodyLarge,
        )
        LabelledValue(
            label = stringResource(R.string.label_build),
            value = "${state.buildType} ${state.versionName}",
        )
        state.environment?.let { environment ->
            LabelledValue(label = stringResource(R.string.label_environment), value = environment)
        }
        state.buildLabel?.let { buildLabel ->
            LabelledValue(label = stringResource(R.string.label_build_label), value = buildLabel)
        }
        ConfigurationStatus(state)
    }
}

@Composable
private fun LabelledValue(
    label: String,
    value: String,
) {
    // Merged semantics: screen readers announce label and value as one item.
    Column(modifier = Modifier.fillMaxWidth().semantics(mergeDescendants = true) {}) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Text(text = value, style = MaterialTheme.typography.bodyLarge)
    }
}

@Composable
private fun ConfigurationStatus(state: ScaffoldScreenState) {
    if (state.isConfigured) {
        Text(
            text = stringResource(R.string.configuration_loaded),
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.primary,
        )
    } else {
        Text(
            text = stringResource(R.string.configuration_incomplete, state.problems.joinToString()),
            style = MaterialTheme.typography.bodyLarge,
            color = MaterialTheme.colorScheme.error,
        )
    }
}

@Preview(showBackground = true)
@Composable
private fun ScaffoldScreenLoadedPreview() {
    PenniLogicTheme {
        RootSurface {
            ScaffoldScreen(
                ScaffoldScreenState(
                    buildType = "debug",
                    versionName = "0.1.0-debug",
                    environment = "development",
                    buildLabel = "local",
                    problems = emptyList(),
                ),
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
private fun ScaffoldScreenInvalidPreview() {
    PenniLogicTheme {
        RootSurface {
            ScaffoldScreen(
                ScaffoldScreenState(
                    buildType = "release",
                    versionName = "0.1.0",
                    environment = null,
                    buildLabel = null,
                    problems = listOf("PENNILOGIC_ENVIRONMENT:missing", "PENNILOGIC_API_BASE_URL:missing"),
                ),
            )
        }
    }
}

@Preview(showBackground = true, widthDp = 1000, heightDp = 700)
@Composable
private fun ScaffoldScreenExpandedPreview() {
    PenniLogicTheme {
        RootSurface {
            ScaffoldScreen(
                ScaffoldScreenState(
                    buildType = "debug",
                    versionName = "0.1.0-debug",
                    environment = "development",
                    buildLabel = "local",
                    problems = emptyList(),
                ),
            )
        }
    }
}
