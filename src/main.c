#include "clothing_analyzer.h"

#include <stdio.h>
#include <stdlib.h>
#include <time.h>

static double elapsed_ms(clock_t start, clock_t end)
{
    return ((double)(end - start) * 1000.0) / (double)CLOCKS_PER_SEC;
}

static void print_analysis_json(const ClothingAnalysis *result, int indent)
{
    const char *sp = indent ? "  " : "";
    printf("%s{\n", sp);
    printf("%s  \"upper_color\": \"%s\",\n", sp, result->upper_color);
    printf("%s  \"lower_color\": \"%s\",\n", sp, result->lower_color);
    printf("%s  \"lower_garment\": \"%s\",\n", sp, result->lower_garment);
    printf("%s  \"pants_length\": \"%s\",\n", sp, result->pants_length);
    printf("%s  \"exposure\": \"%s\",\n", sp, result->exposure);
    printf("%s  \"skin_ratio\": %.4f,\n", sp, result->skin_ratio);
    printf("%s  \"upper_skin_ratio\": %.4f,\n", sp, result->upper_skin_ratio);
    printf("%s  \"lower_skin_ratio\": %.4f,\n", sp, result->lower_skin_ratio);
    printf("%s  \"lower_coverage_ratio\": %.4f,\n", sp, result->lower_coverage_ratio);
    printf("%s  \"lower_split_ratio\": %.4f,\n", sp, result->lower_split_ratio);
    printf("%s  \"lower_center_fill_ratio\": %.4f,\n", sp, result->lower_center_fill_ratio);
    printf("%s  \"person_confidence\": %.4f,\n", sp, result->person_confidence);
    printf("%s  \"color_confidence\": %.4f,\n", sp, result->color_confidence);
    printf("%s  \"analysis_quality\": \"%s\",\n", sp, result->analysis_quality);
    printf("%s  \"color_quality\": \"%s\",\n", sp, result->color_quality);
    printf("%s  \"subject_bbox\": [%d, %d, %d, %d],\n",
           sp,
           result->subject_x0,
           result->subject_y0,
           result->subject_x1,
           result->subject_y1);
    printf("%s  \"elapsed_ms\": %.3f\n", sp, result->elapsed_ms);
    printf("%s}", sp);
}

static int analyze_one(const char *path, ClothingAnalysis *out)
{
    Image image = {0};
    if (!load_ppm(path, &image)) {
        return 0;
    }

    clock_t start = clock();
    *out = analyze_clothing(&image);
    clock_t end = clock();
    out->elapsed_ms = elapsed_ms(start, end);

    free_image(&image);
    return 1;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s image.ppm [image2.ppm ...]\n", argv[0]);
        return 2;
    }

    if (argc == 2) {
        ClothingAnalysis result;
        if (!analyze_one(argv[1], &result)) {
            fprintf(stderr, "failed to load PPM image: %s\n", argv[1]);
            return 1;
        }
        print_analysis_json(&result, 0);
        printf("\n");
        return 0;
    }

    printf("[\n");
    for (int i = 1; i < argc; i++) {
        ClothingAnalysis result;
        if (i > 1) {
            printf(",\n");
        }
        if (!analyze_one(argv[i], &result)) {
            printf("  {\"error\": \"failed to load PPM image\"}");
            continue;
        }
        print_analysis_json(&result, 1);
    }
    printf("\n]\n");
    return 0;
}
