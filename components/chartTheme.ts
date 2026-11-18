import {ColorType, CrosshairMode, LineStyle} from 'lightweight-charts';

// TradingView dark palette. Taiwan convention: red is up, green is down.
export const tv = {
  background: '#131722', text: '#d1d4dc', grid: 'rgba(42, 46, 57, 0.6)', border: '#2a2e39',
  up: '#f23645', down: '#089981', upVolume: 'rgba(242, 54, 69, 0.45)', downVolume: 'rgba(8, 153, 129, 0.45)',
};

export const chartOptions = {
  layout: {
    background: {type: ColorType.Solid, color: tv.background}, textColor: tv.text, fontSize: 12,
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Trebuchet MS', Roboto, Ubuntu, sans-serif",
    // Lightweight Charts is TradingView's open-source library; keep its attribution visible.
    attributionLogo: true,
    panes: {separatorColor: tv.border, separatorHoverColor: 'rgba(41, 98, 255, 0.25)', enableResize: true},
  },
  grid: {vertLines: {color: tv.grid}, horzLines: {color: tv.grid}},
  crosshair: {
    mode: CrosshairMode.Normal,
    vertLine: {color: '#9598a1', style: LineStyle.Dashed, labelBackgroundColor: '#2a2e39'},
    horzLine: {color: '#9598a1', style: LineStyle.Dashed, labelBackgroundColor: '#2a2e39'},
  },
  rightPriceScale: {borderColor: tv.border},
  leftPriceScale: {borderColor: tv.border},
  timeScale: {borderColor: tv.border},
};
