<script lang="ts">
  import { onMount } from 'svelte';
  import * as echarts from 'echarts';

  let { option, height = '320px' }: { option: unknown; height?: string } = $props();

  let el: HTMLDivElement;
  let chart: echarts.ECharts | undefined;

  onMount(() => {
    chart = echarts.init(el, undefined, { renderer: 'canvas' });
    if (option) chart.setOption(option as echarts.EChartsOption);
    const ro = new ResizeObserver(() => chart?.resize());
    ro.observe(el);
    return () => {
      ro.disconnect();
      chart?.dispose();
    };
  });

  // Re-render whenever the option object changes.
  $effect(() => {
    if (chart && option) chart.setOption(option as echarts.EChartsOption, true);
  });
</script>

<div bind:this={el} class="chart" style:height></div>
