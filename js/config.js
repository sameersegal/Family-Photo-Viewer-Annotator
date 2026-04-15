/**
 * Family Photo Album - Configuration
 *
 * Update these settings for your deployment:
 * - Set imageSource to 'r2' and fill in R2 details for production
 * - Set api.workerUrl to your Cloudflare Worker URL for shared annotations
 * - Leave workerUrl empty to use localStorage (single device only)
 */
export const CONFIG = {
  // Image source: 'local' (./images/) or 'r2' (Cloudflare R2)
  imageSource: 'r2',

  // Cloudflare R2 settings
  r2: {
    publicUrl: 'https://pub-68327dca334d42bf90cf87e6f62b96fe.r2.dev',
    fullPath: '/full',
    thumbPath: '/thumbs',
  },

  // Local image settings
  localImagePath: './images/',
  localThumbPath: './images/',

  // Worker API configuration (leave workerUrl empty to use localStorage)
  api: {
    workerUrl: 'https://family-album-api.sameersegal.workers.dev',
  },

  // Slideshow settings
  slideshow: {
    autoAdvanceMs: 6000,
    fadeMs: 1500,
    preloadCount: 3,
    showAnnotations: true,
    annotationDisplayMs: 4000,
  },

  // App settings
  app: {
    title: "Ajja's Photos",
    subtitle: 'Photos & Memories',
    frameImage: 'assets/frame.png',
  },
};
