//
//  PIVImageWindow.mm
//  Spim Interface
//
//  Created by Jonny Taylor on 30/10/2016.
//
//

#include "PIVImageWindow.h"
// #include "tmmintrin.h"		// SSSE3 (supplemental SSE3)

template <class TYPE> TYPE SadFunc(TYPE a, TYPE b);
template<> double SadFunc<double>(double a, double b) { return fabs(a - b); }
template<> int SadFunc<int>(int a, int b) { return abs(a - b); }
// Things get messy for the 8- and 16-bit cases because we were working with an unsigned type!
// Fortunately this shouldn't get called, since I have included a template specialization for that case.
template<> unsigned char SadFunc<unsigned char>(unsigned char a, unsigned char b) { return (unsigned char)abs(int(a) - int(b)); }
template<> unsigned short SadFunc<unsigned short>(unsigned short a, unsigned short b) { return (unsigned short)abs(int(a) - int(b)); }

template<> IntegerPoint ImageWindow<double>::CalculateFlowPeakInteger(void) const
{
	// Look for the location of the minimum (positive-valued...) value in the correlation array
	// We insist on an odd-dimensioned correlation matrix in order to make things simpler
	ALWAYS_ASSERT(width & 1);
	ALWAYS_ASSERT(height & 1);
	ALWAYS_ASSERT(width == elementsPerRow);
	double minVal = DBL_MAX;
	int minX = -1, minY = -1;
	IntegerPoint result(-1,-1);
	for (int y = 0; y < height; y++)
		for (int x = 0; x < width; x++)
		{
			if (PixelXY(x, y) < minVal)
			{
				minVal = PixelXY(x, y);
				minX = x;
				minY = y;
				result = IntegerPoint(x, y);
			}
		}
    return result;
}
	
template<> coord2 ImageWindow<double>::CalculateFlowPeak(void) const
{
    IntegerPoint resultInt = CalculateFlowPeakInteger();
    int minX = resultInt.x, minY = resultInt.y;
    coord2 result(minX-width/2, minY-height/2);

    // Sub-pixel parabolic fit
    // This code is taken from the python code in openPIV
	if ((minX > 0) && (minX < width-1))
	{
		double cl = PixelXY(minX-1, minY);
		double c = PixelXY(minX, minY);
		double cr = PixelXY(minX+1, minY);
		result.x += (cl-cr)/(2*cl-4*c+2*cr);
	}
	if ((minY > 0) && (minY < height-1))
	{
		double cu = PixelXY(minX, minY+1);
		double c = PixelXY(minX, minY);
		double cd = PixelXY(minX, minY-1);
        result.y += (cd-cu)/(2*cd-4*c+2*cu);
	}
    
	return result;
}

template<> double ImageWindow<double>::CalculateSNR(int threshold) const
{
    // First determine the integer location of the peak value (minimum of SAD)
    IntegerPoint peakPos = CalculateFlowPeakInteger();
    
    // Look for the smallest value outside a region around that point
    double nextMinVal = DBL_MAX;
    for (int y = 0; y < height; y++)
        for (int x = 0; x < width; x++)
        {
            if ((abs(x-peakPos.x) > threshold) ||
                (abs(y-peakPos.y) > threshold))
            {
                if (PixelXY(x, y) < nextMinVal)
                    nextMinVal = PixelXY(x, y);
            }
        }
    
    // Calculate the ratio of values.
    // Due to our use of SAD, we cannot interpret this the same way as would be done in standard PIV,
    // but it should at least be reasonable to say that a larger difference is good!
    return nextMinVal / PixelXY(peakPos.x, peakPos.y);
}

#pragma mark -

//#include "tmmintrin.h"		// SSSE3 (supplemental SSE3)

inline int SumOver32BitInts(void *i)
{
    uint32_t *l = (uint32_t *)i;
    return l[0] + l[1] + l[2] + l[3];
}

inline int OrOver32BitInts(void *i)
{
    uint32_t *l = (uint32_t *)i;
    return l[0] | l[1] | l[2] | l[3];
}

template<int correlationType, class TYPE> void CrossCorrelateImageWindows(ImageWindow<TYPE> &window1, ImageWindow<TYPE> &window2, ImageWindow<double> &result)
{
    // Generic version
    // For every possible shift of 'a' relative to 'b', calculate the SAD
    int w1Width = window1.width;
    int w1Height = window1.height;
	int maxDX = window2.width - window1.width;
	int maxDY = window2.height - window1.height;
	
    for (int dy = 0; dy <= maxDY; dy++)
        for (int dx = 0; dx <= maxDX; dx++)
        {
            double sum = 0;
            if (correlationType == kCorrelationSAD)
            {
                // Sum of absolute differences
                for (int y = 0; y < w1Height; y++)
                    for (int x = 0; x < w1Width; x++)
                    {
                        sum += SadFunc<TYPE>(window1.PixelXY(x,y), window2.PixelXY(x+dx,y+dy));
                    }
            }
            else if (correlationType == kCorrelationSSD)
            {
                // Sum of squared differences
                for (int y = 0; y < w1Height; y++)
                    for (int x = 0; x < w1Width; x++)
                    {
                        double diff = (window1.PixelXY(x,y) - window2.PixelXY(x+dx,y+dy));
                        sum += diff*diff;
                    }
            }
            else
            {
                // Direct cross-correlation
				ALWAYS_ASSERT(correlationType == kCorrelationDCC);
                for (int y = 0; y < w1Height; y++)
                    for (int x = 0; x < w1Width; x++)
                    {
                        sum -= (window1.PixelXY(x,y) * window2.PixelXY(x+dx,y+dy)); // Negative is to ensure we find the peak minimum
                    }
            }
            result.SetXY(dx, dy, sum);
        }
}

template<> void CrossCorrelateImageWindows<kCorrelationSAD, unsigned char>(ImageWindow<unsigned char> &window1, ImageWindow<unsigned char> &window2, ImageWindow<double> &result)
{
    // Specialized version for SAD with 8-bit data
    // For every possible shift of 'a' relative to 'b', calculate the SAD
    int w1Width = window1.width;
    int w1Height = window1.height;
	int maxDX = window2.width - window1.width;
	int maxDY = window2.height - window1.height;
    for (int dy = 0; dy <= maxDY; dy++)
        for (int dx = 0; dx <= maxDX; dx++)
        {
            double sum = 0;
            //__m128i sumVec = (__m128i)_mm_setzero_ps();
            for (int y = 0; y < w1Height; y++)
            {
                int x = 0;
                for (; x <= w1Width - 16; x += 16)
                    //sumVec = _mm_add_epi64(sumVec, _mm_sad_epu8(_mm_loadu_si128((__m128i*)window1.PixelXYAddr(x, y)), _mm_loadu_si128((__m128i*)window2.PixelXYAddr(x+dx, y+dy))));
                for (; x < w1Width; x++)
                    sum += abs(window1.PixelXY(x, y) - window2.PixelXY(x+dx, y+dy));
            }
            sum += ExtractLongLongPairSum(&sumVec);
            result.SetXY(dx, dy, sum);
        }
}

template<> void CrossCorrelateImageWindows<kCorrelationSAD, unsigned short>(ImageWindow<unsigned short> &window1, ImageWindow<unsigned short> &window2, ImageWindow<double> &result)
{
    // Specialized version for SAD with 16-bit data
    // For every possible shift of 'a' relative to 'b', calculate the SAD
    int w1Width = window1.width;
    int w1Height = window1.height;
	int maxDX = window2.width - window1.width;
	int maxDY = window2.height - window1.height;
	//__m128i zeros = _mm_set1_epi16(0);
	
#ifdef Py_ERRORS_H
	if (maxDX * maxDY >= (1<<15))
		PyErr_Format(PyErr_NewException((char*)"exceptions.TypeError", NULL, NULL), "WOAH - that's a seriously big correlation matrix! This integer-based SAD code only accepts IWs that lead to correlation matrices with up to 2^15 entries.");
#else
	ALWAYS_ASSERT(maxDX * maxDY < (1<<15));
#endif
	
#if 1
    /*  There may be specific circumstances where I want to force the IWs to be smaller in size, but to still be centered
        in the same places as they would be if they were larger. Under those circumstances it is not trivial to provide
        the correct PIV settings to make that happen, and it's easier to leave the PIV settings as they are but to hack
        this function to reduce the actual area over which we do the processing.
        To do that, set inset to a positive value.  */
    const int inset = 0;
    
    // Do the main comparison loop
	for (int dy = 0; dy <= maxDY; dy++)
        for (int dx = 0; dx <= maxDX; dx++)
        {
            double sum = 0;
            //__m128i sumVec = (__m128i)_mm_setzero_ps();
            for (int y = inset; y < w1Height-inset; y++)
            {
                int x = inset;
                for (; x <= w1Width - 8-inset; x += 8)
				{
					//__m128i a = _mm_loadu_si128((__m128i*)window1.PixelXYAddr(x, y));
					//__m128i b = _mm_loadu_si128((__m128i*)window2.PixelXYAddr(x+dx, y+dy));
					/*	Unpack the low/high (unsigned) shorts into ints and then do the SAD processing on ints.
					 Note that I don't believe we can do this in one go, on 16-bit ints all the way.
					 The _mm_madd_epi16 instruction is handy, but subtracting two 16-bit ints will
					 overflow a 16-bit int	*/
					//__m128i oddA = _mm_unpacklo_epi16(a, zeros);		// Check this is the right byte order. I think it is...
					//__m128i oddB = _mm_unpacklo_epi16(b, zeros);
					//__m128i sad = _mm_abs_epi32(_mm_sub_epi32(oddA, oddB));
					//sumVec = _mm_add_epi32(sumVec, sad);
					//__m128i evenA = _mm_unpackhi_epi16(a, zeros);		// Check this is the right byte order. I think it is...
					//__m128i evenB = _mm_unpackhi_epi16(b, zeros);
					//sad = _mm_abs_epi32(_mm_sub_epi32(evenA, evenB));
					//sumVec = _mm_add_epi32(sumVec, sad);
				}
				for (; x < w1Width-inset; x++)
                    sum += abs(window1.PixelXY(x, y) - window2.PixelXY(x+dx, y+dy));
            }
            sum += SumOver32BitInts(&sumVec);
            result.SetXY(dx, dy, sum);
        }
#elif 0
	/*	This variant is slower overall.
	 However, I believe it's the loads that seem to take the time.
	 I say that because adding a lot of extra maths doesn't seem to slow things down at all.
	 I had hoped this would improve the cache usage, but this naive rearrangement hasn't helped.
	 May be worth investigating performance further in future... */
	for (int dy = 0; dy <= maxDY; dy++)
        for (int dx = 0; dx <= maxDX; dx++)
			result[dy][dx] = 0;
	
	for (int dy = 0; dy <= maxDY; dy++)
		for (int y = 0; y < w1Height; y++)
        {
			for (int dx = 0; dx <= maxDX; dx++)
            {
				double sum = 0;
				//__m128i sumVec = (__m128i)_mm_setzero_ps();
                int x = 0;
                for (; x <= w1Width - 8; x += 8)
				{
					//__m128i a = _mm_loadu_si128((__m128i*)window1.PixelXYAddr(x, y));
					//__m128i b = _mm_loadu_si128((__m128i*)window2.PixelXYAddr(x+dx, y+dy));
					/*	Unpack the low/high (unsigned) shorts into ints and then do the SAD processing on ints.
					 Note that I don't believe we can do this in one go, on 16-bit ints all the way.
					 The _mm_madd_epi16 instruction is handy, but subtracting two 16-bit ints will
					 overflow a 16-bit int	*/
					//__m128i oddA = _mm_unpacklo_epi16(a, zeros);		// Check this is the right byte order. I think it is...
					//__m128i oddB = _mm_unpacklo_epi16(b, zeros);
					//__m128i sad = _mm_abs_epi32(_mm_sub_epi32(oddA, oddB));
					//sumVec = _mm_add_epi32(sumVec, sad);
					//__m128i evenA = _mm_unpackhi_epi16(a, zeros);		// Check this is the right byte order. I think it is...
					//__m128i evenB = _mm_unpackhi_epi16(b, zeros);
					//sad = _mm_abs_epi32(_mm_sub_epi32(evenA, evenB));
					//sumVec = _mm_add_epi32(sumVec, sad);
				}
				for (; x < w1Width; x++)
                    sum += abs(window1[y][x] - window2[y+dy][x+dx]);
				sum += SumOver32BitInts(&sumVec);
				result.SetXY(dx, dy, result.PixelXY(dx, dy) + sum);
            }
        }
#endif
}

void Check16BitData(ImageWindow<int> &window1)
{
	// Although this is in principle unnecessary and therefore inefficient, I want to include a test to ensure no values
	// are larger than 2^16-1. The test should be quick, and it will catch what would otherwise be nasty bugs
    int w1Width = window1.width;
    int w1Height = window1.height;
	
	//__m128i orVec = (__m128i)_mm_setzero_ps();
	int orRest = 0;
	for (int y = 0; y < w1Height; y++)
	{
		int x = 0;
		for (; x <= w1Width - 4; x += 4)
			//orVec = _mm_or_si128(orVec, _mm_loadu_si128((__m128i*)window1.PixelXYAddr(x, y)));
		for (; x < w1Width; x++)
			orRest |= window1.PixelXY(x, y);
	}
	int result = orRest | OrOver32BitInts(&orVec);
#ifdef Py_ERRORS_H
	if (result & 0xFFFF0000)
        PyErr_Format(PyErr_NewException((char*)"exceptions.TypeError", NULL, NULL), "ERROR - you passed in values greater than 2^16 - 1 to the fast SAD code!");
#else
	ALWAYS_ASSERT(!(result & 0xFFFF0000));
#endif
}

template<> void CrossCorrelateImageWindows<kCorrelationSAD, int>(ImageWindow<int> &window1, ImageWindow<int> &window2, ImageWindow<double> &result)
{
    // Specialized version for SAD with 32-bit data, BUT we assume we will not overflow an int when we sum across a small IW.
    // This probably implies that it should be used with 16-bit input data, and small IW pixel counts <=2^16 !
    
    // For every possible shift of 'a' relative to 'b', calculate the SAD
    int w1Width = window1.width;
    int w1Height = window1.height;
	int maxDX = window2.width - window1.width;
	int maxDY = window2.height - window1.height;
	
	Check16BitData(window1);
	Check16BitData(window2);
#ifdef Py_ERRORS_H
	if (maxDX * maxDY >= (1<<15))
		PyErr_Format(PyErr_NewException((char*)"exceptions.TypeError", NULL, NULL), "WOAH - that's a seriously big correlation matrix! This integer-based SAD code only accepts IWs that lead to correlation matrices with up to 2^15 entries.");
#else
	ALWAYS_ASSERT(maxDX * maxDY < (1<<15));
#endif
	
	// Now get down to business!
	for (int dy = 0; dy <= maxDY; dy++)
        for (int dx = 0; dx <= maxDX; dx++)
        {
            double sum = 0;
            //__m128i sumVec = (__m128i)_mm_setzero_ps();
            for (int y = 0; y < w1Height; y++)
            {
                int x = 0;
                for (; x <= w1Width - 4; x += 4)
                    //sumVec = _mm_add_epi32(sumVec, _mm_abs_epi32(_mm_sub_epi32(_mm_loadu_si128((__m128i*)window1.PixelXYAddr(x, y)), _mm_loadu_si128((__m128i*)window2.PixelXYAddr(x+dx, y+dy)))));
                for (; x < w1Width; x++)
                    sum += abs(window1.PixelXY(x, y) - window2.PixelXY(x+dx, y+dy));
            }
            sum += SumOver32BitInts(&sumVec);
            result.SetXY(dx, dy, sum);
        }
}

/*	I haven't worked out a neat way of avoiding link errors due to these not being instantiated,
	so I just force their instantiation. I suspect I should just have all the specializations in a header file,
	but that seems a bit messy in terms of dependencies?	*/
template void CrossCorrelateImageWindows<kCorrelationSAD, unsigned short>(ImageWindow<unsigned short> &window1, ImageWindow<unsigned short> &window2, ImageWindow<double> &result);
template void CrossCorrelateImageWindows<kCorrelationSSD, unsigned short>(ImageWindow<unsigned short> &window1, ImageWindow<unsigned short> &window2, ImageWindow<double> &result);
template void CrossCorrelateImageWindows<kCorrelationDCC, unsigned short>(ImageWindow<unsigned short> &window1, ImageWindow<unsigned short> &window2, ImageWindow<double> &result);
template void CrossCorrelateImageWindows<kCorrelationSAD, double>(ImageWindow<double> &window1, ImageWindow<double> &window2, ImageWindow<double> &result);
template void CrossCorrelateImageWindows<kCorrelationSSD, double>(ImageWindow<double> &window1, ImageWindow<double> &window2, ImageWindow<double> &result);
template void CrossCorrelateImageWindows<kCorrelationDCC, double>(ImageWindow<double> &window1, ImageWindow<double> &window2, ImageWindow<double> &result);
template void CrossCorrelateImageWindows<kCorrelationSAD, unsigned char>(ImageWindow<unsigned char> &window1, ImageWindow<unsigned char> &window2, ImageWindow<double> &result);
template void CrossCorrelateImageWindows<kCorrelationSSD, unsigned char>(ImageWindow<unsigned char> &window1, ImageWindow<unsigned char> &window2, ImageWindow<double> &result);
template void CrossCorrelateImageWindows<kCorrelationSAD, int>(ImageWindow<int> &window1, ImageWindow<int> &window2, ImageWindow<double> &result);
template void CrossCorrelateImageWindows<kCorrelationSSD, int>(ImageWindow<int> &window1, ImageWindow<int> &window2, ImageWindow<double> &result);
