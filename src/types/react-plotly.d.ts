declare module "plotly.js-basic-dist-min" {
  const Plotly: {
    newPlot(
      root: HTMLDivElement,
      data: Array<Record<string, unknown>>,
      layout?: Record<string, unknown>,
      config?: Record<string, unknown>,
    ): Promise<void>;
    react(
      root: HTMLDivElement,
      data: Array<Record<string, unknown>>,
      layout?: Record<string, unknown>,
      config?: Record<string, unknown>,
    ): Promise<void>;
    purge(root: HTMLDivElement): void;
  };
  export default Plotly;
}
