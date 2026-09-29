package com.pennilogic.android.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.pennilogic.android.R
import com.pennilogic.android.ui.theme.PenniLogicTheme

/** The only screen of the scaffold: build identity and configuration status, read-only. */
@Composable
fun ScaffoldScreen(
    state: ScaffoldScreenState,
    modifier: Modifier = Modifier,
) {
    Scaffold(modifier = modifier.fillMaxSize()) { innerPadding ->
        Column(
            modifier =
                Modifier
                    .fillMaxSize()
                    .padding(innerPadding)
                    .verticalScroll(rememberScrollState())
                    .padding(horizontal = 24.dp, vertical = 32.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(
                text = stringResource(R.string.app_name),
                style = MaterialTheme.typography.headlineMedium,
                modifier = Modifier.semantics { heading() },
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

@Preview(showBackground = true)
@Composable
private fun ScaffoldScreenInvalidPreview() {
    PenniLogicTheme {
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
