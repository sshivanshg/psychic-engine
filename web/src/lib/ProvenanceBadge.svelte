<script lang="ts">
  // Every panel of fetched data carries its provenance — the desk never trusts an unsourced number.
  let { asOf = null, source = null, snapshot = false, pit = false }: {
    asOf?: string | null;
    source?: string | null;
    snapshot?: boolean;
    pit?: boolean;
  } = $props();
</script>

<span class="prov">
  {#if asOf}<span class="prov-i">as of <b>{asOf}</b></span>{/if}
  {#if source}<span class="prov-i">{source}</span>{/if}
  {#if pit}<span class="prov-i prov-pit">point-in-time</span>{/if}
  {#if snapshot}<span class="prov-i prov-snap">snapshot · eval-barred</span>{/if}
</span>
