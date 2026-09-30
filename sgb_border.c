#include "sgb_border.h"

#include <gb/gb.h>
#include <stdint.h>
#include <gb/sgb.h>
#include <string.h>

#define SGB_CHR_BLOCK0 0
#define SGB_CHR_BLOCK1 1

#define SGB_SCR_FREEZE 1
#define SGB_SCR_UNFREEZE 0

#define SGB_TRANSFER(A,B) map_buf[0]=(A),map_buf[1]=(B),sgb_transfer(map_buf) 

// The display must be turned on before calling this function
// (with @ref DISPLAY_ON).
void set_sgb_border(unsigned char * tiledata, size_t tiledata_size,
                    unsigned char * tilemap, size_t tilemap_size,
                    unsigned char * palette, size_t palette_size) {
    if (sgb_check()) {
        unsigned char map_buf[20];
        memset(map_buf, 0, sizeof(map_buf));

        SGB_TRANSFER((SGB_MASK_EN << 3) | 1, SGB_SCR_FREEZE); 

        BGP_REG = OBP0_REG = OBP1_REG = 0xE4U;
        SCX_REG = SCY_REG = 0U;

        uint8_t tmp_lcdc = LCDC_REG;

        HIDE_SPRITES, HIDE_WIN, SHOW_BKG;
        DISPLAY_ON;
        // prepare tilemap for SGB_BORDER_CHR_TRN (should display all 256 tiles)
        uint8_t i = 0U;
        for (uint8_t y = 0; y != 14U; ++y) {
            uint8_t * dout = map_buf;
            for (uint8_t x = 0U; x != 20U; ++x) {
                *dout++ = i++;
            }
            set_bkg_tiles(0, y, 20, 1, map_buf);
        }
        memset(map_buf, 0, sizeof(map_buf));

        // transfer tile data. The SGB reads every _TRN payload off the rendered
        // screen (tiles $00-$FF shown in order above), reconstructing the bytes
        // as stored at GB VRAM $8000-$8FFF -- for BOTH blocks, so block 1 works
        // by reloading the second half of the data at tile 0.
        // Two GB bugs to dodge: set_bkg_data takes a uint8_t tile count, so
        // 256 tiles (a full block) must go out as 255 + 1; and the stock
        // example shipped set_bkg_data(0, 0, ...) here, transferring nothing.
        uint8_t ntiles = (tiledata_size > 256 * 32) ? 0 : tiledata_size >> 5;
        if (ntiles > 128U) {
            set_bkg_data(0, 255, tiledata);
            set_bkg_data(255, 1, tiledata + 255 * 16);
            SGB_TRANSFER((SGB_CHR_TRN << 3) | 1, SGB_CHR_BLOCK0);
            tiledata += (128 * 32);
            ntiles -= 128U;
            set_bkg_data(0, ntiles << 1, tiledata);
            SGB_TRANSFER((SGB_CHR_TRN << 3) | 1, SGB_CHR_BLOCK1);
        } else {
            set_bkg_data(0, ntiles << 1, tiledata); 
            SGB_TRANSFER((SGB_CHR_TRN << 3) | 1, SGB_CHR_BLOCK0);
        }

        // transfer map and palettes
        set_bkg_data(0, (uint8_t)(tilemap_size >> 4), tilemap);
        set_bkg_data(128, (uint8_t)(palette_size >> 4), palette);
        SGB_TRANSFER((SGB_PCT_TRN << 3) | 1, 0);

        LCDC_REG = tmp_lcdc;

        // clear SCREEN
        fill_bkg_rect(0, 0, 20, 18, 0);
        
        SGB_TRANSFER((SGB_MASK_EN << 3) | 1, SGB_SCR_UNFREEZE); 
    }
}
