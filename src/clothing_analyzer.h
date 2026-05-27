#ifndef CLOTHING_ANALYZER_H
#define CLOTHING_ANALYZER_H

#include <stddef.h>

typedef struct {
    int width;
    int height;
    unsigned char *rgb;
} Image;

typedef struct {
    const char *upper_color;
    const char *lower_color;
    const char *pants_length;
    const char *exposure;
    double skin_ratio;
    double upper_skin_ratio;
    double lower_skin_ratio;
    double lower_coverage_ratio;
    double person_confidence;
    double color_confidence;
    const char *analysis_quality;
    const char *color_quality;
    int subject_x0;
    int subject_y0;
    int subject_x1;
    int subject_y1;
    double elapsed_ms;
} ClothingAnalysis;

int load_ppm(const char *path, Image *out);
void free_image(Image *image);
ClothingAnalysis analyze_clothing(const Image *image);

#endif
