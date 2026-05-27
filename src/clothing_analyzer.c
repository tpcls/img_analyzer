#include "clothing_analyzer.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int x0;
    int y0;
    int x1;
    int y1;
} Rect;

typedef struct {
    double h;
    double s;
    double v;
} Hsv;

typedef struct {
    double r;
    double g;
    double b;
} ColorGain;

typedef struct {
    double split_ratio;
    double center_fill_ratio;
} LowerShape;

static int weighted_quantile(const double *values, int n, double q)
{
    double total = 0.0;
    double acc = 0.0;
    for (int i = 0; i < n; i++) {
        total += values[i];
    }
    if (total <= 0.0) {
        return 0;
    }
    double target = total * q;
    for (int i = 0; i < n; i++) {
        acc += values[i];
        if (acc >= target) {
            return i;
        }
    }
    return n - 1;
}

static int read_token(FILE *fp, char *buffer, size_t size)
{
    int c = 0;
    size_t len = 0;

    do {
        c = fgetc(fp);
        if (c == '#') {
            while (c != '\n' && c != EOF) {
                c = fgetc(fp);
            }
        }
    } while (isspace(c));

    if (c == EOF) {
        return 0;
    }

    while (c != EOF && !isspace(c)) {
        if (len + 1 < size) {
            buffer[len++] = (char)c;
        }
        c = fgetc(fp);
    }
    buffer[len] = '\0';
    return len > 0;
}

int load_ppm(const char *path, Image *out)
{
    char token[64];
    FILE *fp = fopen(path, "rb");
    if (!fp) {
        return 0;
    }

    if (!read_token(fp, token, sizeof(token))) {
        fclose(fp);
        return 0;
    }

    int binary = strcmp(token, "P6") == 0;
    int ascii = strcmp(token, "P3") == 0;
    if (!binary && !ascii) {
        fclose(fp);
        return 0;
    }

    if (!read_token(fp, token, sizeof(token))) {
        fclose(fp);
        return 0;
    }
    int width = atoi(token);

    if (!read_token(fp, token, sizeof(token))) {
        fclose(fp);
        return 0;
    }
    int height = atoi(token);

    if (!read_token(fp, token, sizeof(token))) {
        fclose(fp);
        return 0;
    }
    int max_value = atoi(token);

    if (width <= 0 || height <= 0 || max_value <= 0 || max_value > 255) {
        fclose(fp);
        return 0;
    }

    size_t count = (size_t)width * (size_t)height * 3u;
    unsigned char *rgb = (unsigned char *)malloc(count);
    if (!rgb) {
        fclose(fp);
        return 0;
    }

    if (binary) {
        if (fread(rgb, 1, count, fp) != count) {
            free(rgb);
            fclose(fp);
            return 0;
        }
    } else {
        for (size_t i = 0; i < count; i++) {
            if (!read_token(fp, token, sizeof(token))) {
                free(rgb);
                fclose(fp);
                return 0;
            }
            int value = atoi(token);
            if (value < 0) {
                value = 0;
            } else if (value > max_value) {
                value = max_value;
            }
            rgb[i] = (unsigned char)((value * 255) / max_value);
        }
    }

    fclose(fp);
    out->width = width;
    out->height = height;
    out->rgb = rgb;
    return 1;
}

void free_image(Image *image)
{
    free(image->rgb);
    image->rgb = NULL;
    image->width = 0;
    image->height = 0;
}

static const unsigned char *pixel_at(const Image *image, int x, int y)
{
    return &image->rgb[((size_t)y * (size_t)image->width + (size_t)x) * 3u];
}

static double color_distance_sq(const unsigned char *p, double r, double g, double b)
{
    double dr = (double)p[0] - r;
    double dg = (double)p[1] - g;
    double db = (double)p[2] - b;
    return dr * dr + dg * dg + db * db;
}

static Hsv rgb_to_hsv(unsigned char r8, unsigned char g8, unsigned char b8)
{
    double r = (double)r8 / 255.0;
    double g = (double)g8 / 255.0;
    double b = (double)b8 / 255.0;
    double maxc = fmax(r, fmax(g, b));
    double minc = fmin(r, fmin(g, b));
    double delta = maxc - minc;
    Hsv hsv = {0.0, 0.0, maxc};

    if (delta > 1e-9) {
        hsv.s = maxc <= 0.0 ? 0.0 : delta / maxc;
        if (maxc == r) {
            hsv.h = 60.0 * fmod(((g - b) / delta), 6.0);
        } else if (maxc == g) {
            hsv.h = 60.0 * (((b - r) / delta) + 2.0);
        } else {
            hsv.h = 60.0 * (((r - g) / delta) + 4.0);
        }
        if (hsv.h < 0.0) {
            hsv.h += 360.0;
        }
    }

    return hsv;
}

static unsigned char clamp_u8(double value)
{
    if (value < 0.0) {
        return 0;
    }
    if (value > 255.0) {
        return 255;
    }
    return (unsigned char)(value + 0.5);
}

static void corrected_pixel(const unsigned char *p, ColorGain gain, unsigned char out[3])
{
    out[0] = clamp_u8((double)p[0] * gain.r);
    out[1] = clamp_u8((double)p[1] * gain.g);
    out[2] = clamp_u8((double)p[2] * gain.b);
}

static int is_skin_pixel(const unsigned char *p)
{
    int r = p[0];
    int g = p[1];
    int b = p[2];
    double y = 0.299 * r + 0.587 * g + 0.114 * b;
    double cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b;
    double cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b;
    Hsv hsv = rgb_to_hsv(p[0], p[1], p[2]);
    int rgb_rule = r > 60 && g > 35 && b > 20 && r > g && r > b && abs(r - g) > 8 && abs(r - g) < 90;
    int ycbcr_rule = y > 45.0 && cb >= 77.0 && cb <= 135.0 && cr >= 133.0 && cr <= 180.0;
    int hsv_rule = (hsv.h <= 55.0 || hsv.h >= 345.0) && hsv.s >= 0.12 && hsv.s <= 0.72 && hsv.v >= 0.22;
    int not_stage_pink = !(r > 170 && b > 135 && g < 130 && abs(r - b) < 70);
    return rgb_rule && ycbcr_rule && hsv_rule && not_stage_pink;
}

static int is_exposed_skin_pixel(const unsigned char *p)
{
    if (is_skin_pixel(p)) {
        return 1;
    }

    int r = p[0];
    int g = p[1];
    int b = p[2];
    double y = 0.299 * r + 0.587 * g + 0.114 * b;
    double cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b;
    double cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b;
    Hsv hsv = rgb_to_hsv(p[0], p[1], p[2]);
    int blue_stage_skin =
        y > 125.0 && cb >= 135.0 && cb <= 168.0 && cr >= 92.0 && cr <= 148.0 &&
        hsv.h >= 185.0 && hsv.h <= 225.0 && hsv.s >= 0.10 && hsv.s <= 0.42 && hsv.v >= 0.58;
    int purple_stage_skin =
        y > 95.0 && cb >= 130.0 && cb <= 160.0 && cr >= 128.0 && cr <= 158.0 &&
        hsv.h >= 255.0 && hsv.h <= 325.0 && hsv.s >= 0.10 && hsv.s <= 0.42 && hsv.v >= 0.45;
    return blue_stage_skin || purple_stage_skin;
}

static int is_dark_neutral(const Hsv hsv)
{
    int blue_stage_black = hsv.v < 0.54 && hsv.h >= 185.0 && hsv.h <= 265.0;
    return hsv.v < 0.28 || (hsv.v < 0.40 && hsv.s < 0.58) || blue_stage_black;
}

static int is_light_neutral(const Hsv hsv)
{
    return hsv.s < 0.34 && hsv.v > 0.72;
}

static int is_gray_neutral(const Hsv hsv)
{
    return hsv.s < 0.18 && hsv.v >= 0.20 && hsv.v <= 0.76;
}

static const char *color_name_from_hsv(Hsv hsv)
{
    if (is_dark_neutral(hsv)) {
        return "black";
    }
    if (is_light_neutral(hsv)) {
        return "white";
    }
    if (is_gray_neutral(hsv)) {
        return "gray";
    }
    if (hsv.h < 15.0 || hsv.h >= 345.0) {
        return "red";
    }
    if (hsv.h < 38.0) {
        return hsv.v < 0.55 ? "brown" : "orange";
    }
    if (hsv.h < 68.0) {
        return "yellow";
    }
    if (hsv.h < 165.0) {
        return "green";
    }
    if (hsv.h < 200.0) {
        return "cyan";
    }
    if (hsv.h < 255.0) {
        return "blue";
    }
    if (hsv.h < 290.0) {
        return "purple";
    }
    if (hsv.h < 345.0) {
        return "pink";
    }
    return "unknown";
}

static Rect estimate_subject(const Image *image)
{
    const int w = image->width;
    const int h = image->height;
    double br = 0.0;
    double bg = 0.0;
    double bb = 0.0;
    int border_count = 0;

    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            if (x < w / 20 || x >= w - w / 20 || y < h / 20 || y >= h - h / 20) {
                const unsigned char *p = pixel_at(image, x, y);
                br += p[0];
                bg += p[1];
                bb += p[2];
                border_count++;
            }
        }
    }

    if (border_count > 0) {
        br /= border_count;
        bg /= border_count;
        bb /= border_count;
    }

    int *row_counts = (int *)calloc((size_t)h, sizeof(int));
    int *col_counts = (int *)calloc((size_t)w, sizeof(int));
    double *detail_rows = (double *)calloc((size_t)h, sizeof(double));
    double *detail_cols = (double *)calloc((size_t)w, sizeof(double));
    double *skin_cols = (double *)calloc((size_t)w, sizeof(double));
    if (!row_counts || !col_counts || !detail_rows || !detail_cols || !skin_cols) {
        free(row_counts);
        free(col_counts);
        free(detail_rows);
        free(detail_cols);
        free(skin_cols);
        Rect fallback = {w / 4, h / 10, (w * 3) / 4, h - 1};
        return fallback;
    }

    Rect rect = {w, h, -1, -1};
    int threshold = 42;
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            const unsigned char *p = pixel_at(image, x, y);
            double bg_distance_sq = color_distance_sq(p, br, bg, bb);
            Hsv hsv = rgb_to_hsv(p[0], p[1], p[2]);
            double center = 1.0 - fabs(((double)x + 0.5) / (double)w - 0.5) * 2.0;
            int skin = is_skin_pixel(p);
            int dark_detail = hsv.v < 0.28 && bg_distance_sq > 18.0 * 18.0;
            int strong_edge = bg_distance_sq > (double)threshold * threshold && !(hsv.v > 0.72 && hsv.s < 0.18 && bg_distance_sq < 95.0 * 95.0);
            int skin_seed = skin && center > 0.20 && bg_distance_sq > 30.0 * 30.0;
            int body_detail = (dark_detail || (bg_distance_sq > 82.0 * 82.0 && hsv.v < 0.86 && hsv.s > 0.16)) && center > 0.10;
            int foreground = dark_detail || strong_edge || skin_seed;
            if (foreground) {
                row_counts[y]++;
                col_counts[x]++;
                if (x < rect.x0) {
                    rect.x0 = x;
                }
                if (x > rect.x1) {
                    rect.x1 = x;
                }
                if (y < rect.y0) {
                    rect.y0 = y;
                }
                if (y > rect.y1) {
                    rect.y1 = y;
                }
            }
            if (body_detail) {
                double weight = 0.35 + center * center * 2.0;
                detail_rows[y] += weight;
                detail_cols[x] += weight;
            }
            if (skin) {
                skin_cols[x] += 1.0 + center;
            }
        }
    }

    if (rect.x1 < rect.x0 || rect.y1 < rect.y0) {
        rect.x0 = w / 4;
        rect.x1 = (w * 3) / 4;
        rect.y0 = h / 10;
        rect.y1 = h - 1;
    }

    int min_col_density = h / 35;
    int min_row_density = w / 45;
    int center_x = w / 2;
    int x0 = center_x;
    int x1 = center_x;
    while (x0 > 0 && (col_counts[x0] >= min_col_density || x0 > w / 3)) {
        x0--;
    }
    while (x1 + 1 < w && (col_counts[x1] >= min_col_density || x1 < (w * 2) / 3)) {
        x1++;
    }

    int y0 = rect.y0;
    int y1 = rect.y1;
    while (y0 < y1 && row_counts[y0] < min_row_density) {
        y0++;
    }
    while (y1 > y0 && row_counts[y1] < min_row_density) {
        y1--;
    }

    if (x1 - x0 > w / 8 && y1 - y0 > h / 8) {
        rect.x0 = x0;
        rect.x1 = x1;
        rect.y0 = y0;
        rect.y1 = y1;
    }

    double *smooth_cols = (double *)calloc((size_t)w, sizeof(double));
    double *focus_rows = (double *)calloc((size_t)h, sizeof(double));
    int detail_x0 = weighted_quantile(detail_cols, w, 0.02);
    int detail_x1 = weighted_quantile(detail_cols, w, 0.98);
    int detail_y0 = weighted_quantile(detail_rows, h, 0.01);
    int detail_y1 = weighted_quantile(detail_rows, h, 0.995);

    if (smooth_cols && focus_rows) {
        int radius = w / 80;
        if (radius < 3) {
            radius = 3;
        }
        double max_col = 0.0;
        for (int x = 0; x < w; x++) {
            int a = x - radius < 0 ? 0 : x - radius;
            int b = x + radius >= w ? w - 1 : x + radius;
            for (int i = a; i <= b; i++) {
                smooth_cols[x] += detail_cols[i];
            }
            if (smooth_cols[x] > max_col) {
                max_col = smooth_cols[x];
            }
        }

        double threshold_col = max_col * 0.18;
        int best_x0 = -1;
        int best_x1 = -1;
        double best_score = -1.0;
        int x = 0;
        while (x < w) {
            while (x < w && smooth_cols[x] < threshold_col) {
                x++;
            }
            int seg_x0 = x;
            double seg_sum = 0.0;
            while (x < w && smooth_cols[x] >= threshold_col) {
                seg_sum += smooth_cols[x];
                x++;
            }
            int seg_x1 = x - 1;
            int seg_w = seg_x1 - seg_x0 + 1;
            if (seg_w > w / 32) {
                double seg_center = ((double)seg_x0 + (double)seg_x1) * 0.5;
                double center_score = 1.0 - fabs(seg_center / (double)w - 0.5) * 2.0;
                if (center_score < 0.0) {
                    center_score = 0.0;
                }
                double width_penalty = seg_w > (w * 7) / 10 ? 0.35 : 1.0;
                double score = seg_sum * (0.55 + center_score * 1.20) * width_penalty;
                if (score > best_score) {
                    best_score = score;
                    best_x0 = seg_x0;
                    best_x1 = seg_x1;
                }
            }
        }

        if (best_x0 >= 0 && best_x1 > best_x0) {
            int seg_w = best_x1 - best_x0 + 1;
            int sample_x0 = best_x0 - seg_w / 5 < 0 ? 0 : best_x0 - seg_w / 5;
            int sample_x1 = best_x1 + seg_w / 5 >= w ? w - 1 : best_x1 + seg_w / 5;
            int detail_w = detail_x1 - detail_x0 + 1;

            if ((double)h / (double)w < 0.75 && detail_w > (w * 7) / 10) {
                int window = w / 3;
                if (window < w / 4) {
                    window = w / 4;
                }
                int best_window_x0 = sample_x0;
                double best_window_score = -1.0;
                double sum = 0.0;
                double skin_sum = 0.0;
                for (int xx = 0; xx < window; xx++) {
                    sum += detail_cols[xx];
                    skin_sum += skin_cols[xx];
                }
                for (int wx0 = 0; wx0 + window < w; wx0++) {
                    int wx1 = wx0 + window - 1;
                    double mid = ((double)wx0 + (double)wx1) * 0.5;
                    double center_score = 1.0 - fabs(mid / (double)w - 0.5) * 2.0;
                    if (center_score < 0.0) {
                        center_score = 0.0;
                    }
                    double score = (sum + skin_sum * 3.0) * (0.55 + center_score * 1.35);
                    if (score > best_window_score) {
                        best_window_score = score;
                        best_window_x0 = wx0;
                    }
                    if (wx0 + window < w) {
                        sum += detail_cols[wx0 + window] - detail_cols[wx0];
                        skin_sum += skin_cols[wx0 + window] - skin_cols[wx0];
                    }
                }
                sample_x0 = best_window_x0;
                sample_x1 = best_window_x0 + window - 1;
            }

            for (int yy = 0; yy < h; yy++) {
                for (int xx = sample_x0; xx <= sample_x1; xx++) {
                    const unsigned char *p = pixel_at(image, xx, yy);
                    double bg_distance_sq = color_distance_sq(p, br, bg, bb);
                    Hsv hsv = rgb_to_hsv(p[0], p[1], p[2]);
                    double center = 1.0 - fabs(((double)xx + 0.5) / (double)w - 0.5) * 2.0;
                    int dark_detail = hsv.v < 0.28 && bg_distance_sq > 18.0 * 18.0;
                    int body_detail = (dark_detail || (bg_distance_sq > 82.0 * 82.0 && hsv.v < 0.86 && hsv.s > 0.16)) && center > 0.08;
                    if (body_detail || is_skin_pixel(p)) {
                        focus_rows[yy] += 1.0;
                    }
                }
            }

            detail_x0 = sample_x0;
            detail_x1 = sample_x1;
            detail_y0 = weighted_quantile(focus_rows, h, 0.01);
            detail_y1 = weighted_quantile(focus_rows, h, 0.995);
        }
    }

    if (detail_x1 - detail_x0 > w / 10 && detail_y1 - detail_y0 > h / 10) {
        int dx = (detail_x1 - detail_x0 + 1) / 5;
        int dy = (detail_y1 - detail_y0 + 1) / 8;
        rect.x0 = detail_x0 - dx < 0 ? 0 : detail_x0 - dx;
        rect.x1 = detail_x1 + dx >= w ? w - 1 : detail_x1 + dx;
        rect.y0 = detail_y0 - dy < 0 ? 0 : detail_y0 - dy;
        rect.y1 = detail_y1 + dy >= h ? h - 1 : detail_y1 + dy;
    }

    free(smooth_cols);
    free(focus_rows);
    free(row_counts);
    free(col_counts);
    free(detail_rows);
    free(detail_cols);
    free(skin_cols);

    int pad_x = (rect.x1 - rect.x0 + 1) / 18;
    int pad_y = (rect.y1 - rect.y0 + 1) / 30;
    rect.x0 = rect.x0 - pad_x < 0 ? 0 : rect.x0 - pad_x;
    rect.x1 = rect.x1 + pad_x >= w ? w - 1 : rect.x1 + pad_x;
    rect.y0 = rect.y0 - pad_y < 0 ? 0 : rect.y0 - pad_y;
    rect.y1 = rect.y1 + pad_y >= h ? h - 1 : rect.y1 + pad_y;
    return rect;
}

static ColorGain estimate_skin_gain(const Image *image, Rect subject)
{
    double sr = 0.0;
    double sg = 0.0;
    double sb = 0.0;
    int count = 0;
    int h = subject.y1 - subject.y0 + 1;
    Rect skin_zone = subject;
    skin_zone.y0 = subject.y0 + (int)(h * 0.12);
    skin_zone.y1 = subject.y0 + (int)(h * 0.58);

    for (int y = skin_zone.y0; y <= skin_zone.y1; y++) {
        for (int x = skin_zone.x0; x <= skin_zone.x1; x++) {
            const unsigned char *p = pixel_at(image, x, y);
            if (is_skin_pixel(p)) {
                sr += p[0];
                sg += p[1];
                sb += p[2];
                count++;
            }
        }
    }

    ColorGain gain = {1.0, 1.0, 1.0};
    if (count < 40) {
        return gain;
    }

    sr /= count;
    sg /= count;
    sb /= count;
    gain.r = 218.0 / fmax(sr, 1.0);
    gain.g = 166.0 / fmax(sg, 1.0);
    gain.b = 132.0 / fmax(sb, 1.0);

    if (gain.r < 0.72) {
        gain.r = 0.72;
    } else if (gain.r > 1.32) {
        gain.r = 1.32;
    }
    if (gain.g < 0.72) {
        gain.g = 0.72;
    } else if (gain.g > 1.32) {
        gain.g = 1.32;
    }
    if (gain.b < 0.72) {
        gain.b = 0.72;
    } else if (gain.b > 1.32) {
        gain.b = 1.32;
    }
    return gain;
}

static int clothing_pixel(const unsigned char *p)
{
    if (is_skin_pixel(p)) {
        return 0;
    }
    return 1;
}

static const char *dominant_color(const Image *image, Rect zone, ColorGain gain)
{
    double sum_r[13] = {0};
    double sum_g[13] = {0};
    double sum_b[13] = {0};
    int counts[13] = {0};

    for (int y = zone.y0; y <= zone.y1; y++) {
        for (int x = zone.x0; x <= zone.x1; x++) {
            const unsigned char *p = pixel_at(image, x, y);
            if (!clothing_pixel(p)) {
                continue;
            }
            unsigned char corrected[3];
            corrected_pixel(p, gain, corrected);
            Hsv hsv = rgb_to_hsv(corrected[0], corrected[1], corrected[2]);
            int bin = 0;
            if (is_dark_neutral(hsv)) {
                bin = 0;
            } else if (is_light_neutral(hsv)) {
                bin = 1;
            } else if (is_gray_neutral(hsv)) {
                bin = 2;
            } else {
                bin = 3 + (int)(hsv.h / 36.0);
                if (bin > 12) {
                    bin = 12;
                }
            }
            counts[bin]++;
            sum_r[bin] += corrected[0];
            sum_g[bin] += corrected[1];
            sum_b[bin] += corrected[2];
        }
    }

    int best = -1;
    for (int i = 0; i < 13; i++) {
        if (best < 0 || counts[i] > counts[best]) {
            best = i;
        }
    }

    if (best < 0 || counts[best] < 12) {
        return "unknown";
    }

    Hsv avg = rgb_to_hsv((unsigned char)(sum_r[best] / counts[best]),
                         (unsigned char)(sum_g[best] / counts[best]),
                         (unsigned char)(sum_b[best] / counts[best]));
    return color_name_from_hsv(avg);
}

static double skin_ratio_in_rect(const Image *image, Rect rect)
{
    int total = 0;
    int skin = 0;
    for (int y = rect.y0; y <= rect.y1; y++) {
        for (int x = rect.x0; x <= rect.x1; x++) {
            total++;
            if (is_exposed_skin_pixel(pixel_at(image, x, y))) {
                skin++;
            }
        }
    }
    return total == 0 ? 0.0 : (double)skin / (double)total;
}

static double lower_garment_coverage(const Image *image, Rect lower)
{
    int h = lower.y1 - lower.y0 + 1;
    int w = lower.x1 - lower.x0 + 1;
    int lowest = -1;

    for (int y = lower.y0; y <= lower.y1; y++) {
        int row_cloth = 0;
        for (int x = lower.x0; x <= lower.x1; x++) {
            const unsigned char *p = pixel_at(image, x, y);
            if (clothing_pixel(p) && !is_exposed_skin_pixel(p)) {
                row_cloth++;
            }
        }
        if ((double)row_cloth / (double)w > 0.18) {
            lowest = y;
        }
    }

    if (lowest < 0) {
        return 0.0;
    }
    return (double)(lowest - lower.y0 + 1) / (double)h;
}

static double lower_skin_reach(const Image *image, Rect lower)
{
    int h = lower.y1 - lower.y0 + 1;
    int w = lower.x1 - lower.x0 + 1;
    int lowest = -1;

    for (int y = lower.y0; y <= lower.y1; y++) {
        int row_skin = 0;
        for (int x = lower.x0; x <= lower.x1; x++) {
            if (is_exposed_skin_pixel(pixel_at(image, x, y))) {
                row_skin++;
            }
        }
        if ((double)row_skin / (double)w > 0.10) {
            lowest = y;
        }
    }

    if (lowest < 0) {
        return 0.0;
    }
    return (double)(lowest - lower.y0 + 1) / (double)h;
}

static const char *pants_length_from_coverage(double coverage, double lower_skin_ratio, double skin_reach)
{
    if (coverage <= 0.05) {
        return "unknown";
    }
    if (lower_skin_ratio > 0.16 && skin_reach > 0.58) {
        return "shorts";
    }
    if (lower_skin_ratio > 0.42 && coverage < 0.85) {
        return "shorts";
    }
    if (lower_skin_ratio < 0.08 && coverage > 0.92) {
        return "unknown";
    }
    if (lower_skin_ratio > 0.30 && coverage < 0.62) {
        return "knee_length";
    }
    if (coverage < 0.34) {
        return "shorts";
    }
    if (coverage < 0.58) {
        return "knee_length";
    }
    if (coverage < 0.82) {
        return "cropped";
    }
    return "long";
}

static LowerShape lower_shape_metrics(const Image *image, Rect lower, double coverage)
{
    int h = lower.y1 - lower.y0 + 1;
    int w = lower.x1 - lower.x0 + 1;
    int x1 = lower.x0 + w / 3;
    int x2 = lower.x0 + (w * 2) / 3;
    double clamped_coverage = coverage;
    int y0 = lower.y0 + (int)(h * 0.18);
    int y1;
    int usable_rows = 0;
    int split_rows = 0;
    int center_fill_rows = 0;

    if (clamped_coverage < 0.0) {
        clamped_coverage = 0.0;
    } else if (clamped_coverage > 1.0) {
        clamped_coverage = 1.0;
    }
    y1 = lower.y0 + (int)(h * clamped_coverage * 0.98);

    if (y1 <= y0) {
        y1 = lower.y1;
    }
    if (y1 > lower.y1) {
        y1 = lower.y1;
    }

    for (int y = y0; y <= y1; y++) {
        int left_cloth = 0;
        int center_cloth = 0;
        int right_cloth = 0;
        int left_total = 0;
        int center_total = 0;
        int right_total = 0;

        for (int x = lower.x0; x <= lower.x1; x++) {
            const unsigned char *p = pixel_at(image, x, y);
            int is_cloth = clothing_pixel(p) && !is_exposed_skin_pixel(p);
            if (x < x1) {
                left_total++;
                left_cloth += is_cloth;
            } else if (x <= x2) {
                center_total++;
                center_cloth += is_cloth;
            } else {
                right_total++;
                right_cloth += is_cloth;
            }
        }

        double left_ratio = left_total ? (double)left_cloth / (double)left_total : 0.0;
        double center_ratio = center_total ? (double)center_cloth / (double)center_total : 0.0;
        double right_ratio = right_total ? (double)right_cloth / (double)right_total : 0.0;

        if (left_ratio + center_ratio + right_ratio < 0.20) {
            continue;
        }
        usable_rows++;
        if (left_ratio > 0.16 && right_ratio > 0.16 && center_ratio < 0.10) {
            split_rows++;
        }
        if (center_ratio > 0.18) {
            center_fill_rows++;
        }
    }

    LowerShape shape = {0.0, 0.0};
    if (usable_rows > 0) {
        shape.split_ratio = (double)split_rows / (double)usable_rows;
        shape.center_fill_ratio = (double)center_fill_rows / (double)usable_rows;
    }
    return shape;
}

static const char *lower_garment_from_shape(const char *pants_length, double coverage, double lower_skin_ratio, LowerShape shape)
{
    if (strcmp(pants_length, "shorts") == 0) {
        if (shape.center_fill_ratio > 0.58 && shape.split_ratio < 0.08) {
            return "mini_skirt";
        }
        return "shorts";
    }
    if (strcmp(pants_length, "knee_length") == 0) {
        if (shape.center_fill_ratio > 0.52 && shape.split_ratio < 0.12) {
            return "knee_length_skirt";
        }
        return "knee_length_pants";
    }
    if (strcmp(pants_length, "cropped") == 0) {
        if (shape.center_fill_ratio > 0.62 && shape.split_ratio < 0.10 && lower_skin_ratio < 0.28) {
            return "midi_skirt";
        }
        return "cropped_pants";
    }
    if (strcmp(pants_length, "long") == 0) {
        if (shape.center_fill_ratio > 0.68 && shape.split_ratio < 0.08 && coverage > 0.82) {
            return "long_skirt";
        }
        return "long_pants";
    }
    return "unknown";
}

static const char *exposure_from_skin(double total, double upper, double lower)
{
    double score = total * 0.65 + upper * 0.25 + lower * 0.10;
    if (score < 0.12) {
        return "low";
    }
    if (score < 0.28) {
        return "medium";
    }
    return "high";
}

static double clamp01(double value)
{
    if (value < 0.0) {
        return 0.0;
    }
    if (value > 1.0) {
        return 1.0;
    }
    return value;
}

static double estimate_person_confidence(const Image *image, Rect subject, double skin_ratio)
{
    double iw = (double)image->width;
    double ih = (double)image->height;
    double sw = (double)(subject.x1 - subject.x0 + 1);
    double sh = (double)(subject.y1 - subject.y0 + 1);
    double area_ratio = (sw * sh) / (iw * ih);
    double width_ratio = sw / iw;
    double subject_aspect = sh / fmax(sw, 1.0);
    double image_aspect = ih / fmax(iw, 1.0);
    double score = 0.72;

    if (subject_aspect < 0.95) {
        score *= 0.35;
    } else if (subject_aspect < 1.20) {
        score *= 0.65;
    }

    if (image_aspect < 0.75 && width_ratio > 0.88) {
        score *= 0.28;
    } else if (width_ratio > 0.94 && subject_aspect < 1.45) {
        score *= 0.50;
    }

    if (area_ratio < 0.06) {
        score *= 0.45;
    } else if (area_ratio > 0.92 && image_aspect < 1.0) {
        score *= 0.35;
    }

    if (skin_ratio < 0.015) {
        score *= 0.55;
    } else if (skin_ratio > 0.82) {
        score *= 0.75;
    }

    return clamp01(score);
}

static const char *quality_from_confidence(double confidence)
{
    if (confidence >= 0.62) {
        return "high";
    }
    if (confidence >= 0.36) {
        return "medium";
    }
    return "low";
}

static const char *color_quality_from_confidence(double confidence)
{
    if (confidence >= 0.58) {
        return "high";
    }
    if (confidence >= 0.30) {
        return "medium";
    }
    return "low";
}

ClothingAnalysis analyze_clothing(const Image *image)
{
    Rect subject = estimate_subject(image);
    int body_h = subject.y1 - subject.y0 + 1;
    int x_margin = (subject.x1 - subject.x0 + 1) / 8;

    Rect core = subject;
    core.x0 += x_margin;
    core.x1 -= x_margin;
    if (core.x0 > core.x1) {
        core = subject;
    }

    Rect upper = core;
    upper.y0 = subject.y0 + (int)(body_h * 0.18);
    upper.y1 = subject.y0 + (int)(body_h * 0.50);

    Rect lower = core;
    lower.y0 = subject.y0 + (int)(body_h * 0.48);
    lower.y1 = subject.y0 + (int)(body_h * 0.95);

    if (upper.y1 >= image->height) {
        upper.y1 = image->height - 1;
    }
    if (lower.y1 >= image->height) {
        lower.y1 = image->height - 1;
    }

    double skin_total = skin_ratio_in_rect(image, subject);
    double skin_upper = skin_ratio_in_rect(image, upper);
    double skin_lower = skin_ratio_in_rect(image, lower);
    double coverage = lower_garment_coverage(image, lower);
    double skin_reach = lower_skin_reach(image, lower);
    LowerShape lower_shape = lower_shape_metrics(image, lower, coverage);
    ColorGain skin_gain = estimate_skin_gain(image, subject);

    ClothingAnalysis result;
    result.upper_color = dominant_color(image, upper, skin_gain);
    result.lower_color = dominant_color(image, lower, skin_gain);
    result.pants_length = pants_length_from_coverage(coverage, skin_lower, skin_reach);
    result.lower_garment = lower_garment_from_shape(result.pants_length, coverage, skin_lower, lower_shape);
    result.exposure = exposure_from_skin(skin_total, skin_upper, skin_lower);
    result.skin_ratio = skin_total;
    result.upper_skin_ratio = skin_upper;
    result.lower_skin_ratio = skin_lower;
    result.lower_coverage_ratio = coverage;
    result.lower_split_ratio = lower_shape.split_ratio;
    result.lower_center_fill_ratio = lower_shape.center_fill_ratio;
    result.person_confidence = estimate_person_confidence(image, subject, skin_total);
    result.color_confidence = clamp01(result.person_confidence * 0.70 + clamp01(skin_total * 4.0) * 0.30);
    result.analysis_quality = quality_from_confidence(result.person_confidence);
    result.color_quality = color_quality_from_confidence(result.color_confidence);
    result.subject_x0 = subject.x0;
    result.subject_y0 = subject.y0;
    result.subject_x1 = subject.x1;
    result.subject_y1 = subject.y1;
    result.elapsed_ms = 0.0;
    return result;
}
