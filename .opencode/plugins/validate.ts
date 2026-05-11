import type { Plugin } from "@opencode-ai/plugin";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";

const runScript = (scriptPath: string, filePath: string): Promise<string> => {
	return new Promise((resolve, reject) => {
		const proc = spawn("python3", [scriptPath, filePath], {
			stdio: ["ignore", "pipe", "pipe"],
		});
		let stdout = "";
		let stderr = "";
		proc.stdout?.on("data", (d) => (stdout += d.toString()));
		proc.stderr?.on("data", (d) => (stderr += d.toString()));
		proc.on("close", (code) => {
			if (code === 0 || stdout.trim()) {
				resolve(stdout);
			} else {
				reject(new Error(stderr || `exit code ${code}`));
			}
		});
		proc.on("error", reject);
	});
};

const plugin: Plugin = async (input, _options) => {
	return {
		"tool.execute.after": async (toolInput, _toolOutput) => {
			const { tool, args } = toolInput;

			if (tool !== "write" && tool !== "edit") {
				return;
			}

			const filePath = args?.file_path ?? args?.filePath;
			if (!filePath || typeof filePath !== "string") {
				return;
			}

			if (
				!filePath.endsWith(".json") ||
				!filePath.includes("knowledge/articles")
			) {
				return;
			}

			try {
				const validatorPath = `${input.directory}/hooks/validate_json.py`;
				const result = await runScript(validatorPath, filePath);

				if (result.trim()) {
					console.warn(
						`[validate] validation warnings for ${filePath}:\n${result}`,
					);
				}
			} catch (err) {
				console.error(`[validate] failed to run validator: ${err}`);
			}

			try {
				const qualityPath = `${input.directory}/hooks/check_quality.py`;
				const qualityResult = await runScript(qualityPath, filePath);

				if (qualityResult.trim()) {
					console.warn(
						`[validate] quality check for ${filePath}:\n${qualityResult}`,
					);
				}
			} catch (err) {
				console.error(`[validate] failed to run quality checker: ${err}`);
			}
		},
	};
};

export default plugin;
