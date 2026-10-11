import { Chart } from "/js/dist/chart.js-auto.min.js";
const element = document.getElementById("webmail-monitor-data");
if (element) {
	const data = JSON.parse(element.textContent);
	const history = [];
	for (const sample of data.history) {
		const previous = history.at(-1);
		if (previous && sample.time - previous.time > 90) {
			history.push({ time: previous.time + 60, metrics: null });
		}
		history.push(sample);
	}
	new Chart(document.getElementById("webmail-monitor-chart"), {
		type: "line",
		data: {
			labels: history.map((sample) => new Date(sample.time * 1000).toLocaleString()),
			datasets: [
				{
					label: data.active,
					data: history.map((sample) => sample.metrics?.["active processes"] ?? null),
					borderColor: "#3388dd",
					pointRadius: 0,
				},
				{
					label: data.queue,
					data: history.map((sample) => sample.metrics?.["listen queue"] ?? null),
					borderColor: "#dd3388",
					pointRadius: 0,
				},
			],
		},
		options: {
			responsive: true,
			maintainAspectRatio: false,
			animation: false,
			spanGaps: false,
			scales: { y: { beginAtZero: true, ticks: { precision: 0 } } },
		},
	});
}
