declare module "react-plotly.js" {
  import { ComponentType } from "react";

  interface PlotParams {
    data: Array<Record<string, unknown>>;
    layout?: Record<string, unknown>;
    config?: Record<string, unknown>;
    style?: React.CSSProperties;
    className?: string;
    useResizeHandler?: boolean;
  }

  function createPlotlyComponent(plotly: object): ComponentType<PlotParams>;
  export default createPlotlyComponent;
}

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
