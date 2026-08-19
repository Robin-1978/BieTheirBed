import { manipulateAsync, SaveFormat } from "expo-image-manipulator";
import { Image } from "react-native";

import { boundedDimensions } from "./imageBounds";

export type PreparedImageAttachment = {
  uri: string;
  name: string;
  mediaType: "image/jpeg";
  width: number;
  height: number;
};

export async function prepareImageAttachment(
  uri: string,
  name: string,
): Promise<PreparedImageAttachment> {
  const dimensions = await imageDimensions(uri);
  const resized = boundedDimensions(dimensions.width, dimensions.height);
  const actions = resized.width === dimensions.width && resized.height === dimensions.height
    ? []
    : [{ resize: resized.width >= resized.height ? { width: resized.width } : { height: resized.height } }];
  const result = await manipulateAsync(uri, actions, {
    compress: 0.72,
    format: SaveFormat.JPEG,
  });
  return {
    uri: result.uri,
    name: jpegName(name),
    mediaType: "image/jpeg",
    width: result.width,
    height: result.height,
  };
}

function imageDimensions(uri: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve, reject) => {
    Image.getSize(uri, (width, height) => resolve({ width, height }), reject);
  });
}

function jpegName(name: string): string {
  const safe = name.trim() || `image-${Date.now()}`;
  return `${safe.replace(/\.[^.]+$/, "")}.jpg`;
}
